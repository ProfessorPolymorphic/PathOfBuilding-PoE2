"""LLM agent that answers build questions by calling the PoB engine.

The model only decides which engine tools to call and explains the results;
every number comes from the engine. Works with any OpenAI-compatible endpoint
(configured in app/.env; MindRouter + Kimi K3 by default).

The agent is transport-agnostic: give it an async `engine_call(cmd, **args)`
(an EngineWorker.call, or a SessionManager-bound call) and feed it user turns.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

import settings
from engine import EngineError

log = logging.getLogger(__name__)

EngineCall = Callable[..., Awaitable[Any]]

MAX_TOOL_ROUNDS = 12          # model->tools->model cycles per user question
MAX_TOOL_RESULT_CHARS = 16000  # keep one oversized result from eating the context

SYSTEM_PROMPT = """\
You are a build advisor for **Path of Exile 2** (not Path of Exile 1), working with the
user's character loaded into Path of Building (PoB), the community's calculation engine.
A snapshot of the build and its active Configuration settings is provided at the start of
the conversation; use tools for anything else.

## What counts as true
- Numbers: every number you state must come from the build snapshot or a tool result in this
  conversation. Never estimate or recall values from memory.
- How something is calculated: only from explain_stat (PoB's own calculation) or
  stat_sources (the modifiers feeding a stat). Never present a formula or mechanic from memory.
- What a gem, support or skill does: only from describe_skill. What a passive does: only from
  search_passives / rank_passives results. What an item does: only from get_item_details.
- Game mechanics in general (how damage types, ailments, energy shield, recovery, etc. work):
  you have no tool for these, and PoE2 changed many of them from PoE1. Do not state them as
  fact. If one matters, say you can't verify it here.
- If you can't verify something, say so plainly; a short honest answer beats a confident guess.

## How to investigate
- "Why is X low/high?": use explain_stat and/or stat_sources for X before answering, and name
  the specific contributors (items, passives, base/penalty values).
- Before recommending a change (passives, items, config), test it with a compare_* tool and give
  before -> after numbers. Gains don't add up: test combinations in one compare_passives call.
- For "best nodes" questions use rank_passives (one call ranks every candidate).
- Configuration matters: the snapshot lists active settings (e.g. resistance penalty, enemy type,
  conditions). When they explain a number, say so. compare_* tools don't change the build;
  set_config and set_main_skill do. If you change config to test something, restore it
  (value null clears an option) and tell the user.
- You can't search the trade site. To evaluate gear ideas, write a plausible PoE2 item and
  test it with compare_item, labelled as hypothetical.

## Answer style
Don't narrate your process; just answer. Lead with the direct answer, then the evidence (key
numbers, contributors, deltas), then optional next steps. Be concise: short lists or small
tables. Round sensibly.

Useful stat keys (get_stats, explain_stat): Life, EnergyShield, Mana, Spirit, SpiritUnreserved,
TotalDPS, CombinedDPS, FullDPS, AverageHit, Speed, CritChance, CritMultiplier, HitChance,
Armour, Evasion, PhysicalDamageReduction, EvadeChance, DeflectChance, TotalEHP,
FireResist, ColdResist, LightningResist, ChaosResist (and *ResistOverCap),
PhysicalMaximumHitTaken, FireMaximumHitTaken, ColdMaximumHitTaken,
LightningMaximumHitTaken, ChaosMaximumHitTaken, Str, Dex, Int, LifeRegenRecovery.
Modifier names (stat_sources): Life, EnergyShield, Mana, Armour, Evasion, ColdResist,
ElementalResist, ColdResistMax, ElementalResistMax, ChaosResist, Damage, PhysicalDamage,
ElementalDamage, ColdDamage, Speed, CritChance, CritMultiplier, Accuracy, Spirit, ...
"""


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required or []},
    }}


NODE_REFS = {"type": "array", "items": {"type": ["string", "integer"]},
             "description": "Passive node names (exact, case-insensitive) or numeric ids"}

TOOLS = [
    _fn("get_build_summary",
        "Overview of the loaded character: class, ascendancy, level, main skill, allocated "
        "keystones and notables, equipped item names, skill groups with gems, and the "
        "sidebar stats PoB shows for the main skill.", {}),
    _fn("get_stats",
        "Raw engine output values by key (see the system prompt for common keys). "
        "Unknown keys return 'n/a'.",
        {"keys": {"type": "array", "items": {"type": "string"}}}, ["keys"]),
    _fn("explain_stat",
        "PoB's own step-by-step calculation of a stat for this build (the Calcs tab breakdown), "
        "e.g. how a max hit, energy shield, life or attack speed value was derived. Use this "
        "instead of describing formulas from memory.",
        {"stat": {"type": "string", "description": "Output key, e.g. ColdMaximumHitTaken, EnergyShield, Life, Speed"}},
        ["stat"]),
    _fn("stat_sources",
        "Every active modifier feeding one or more modifier names, with its type (BASE/INC/MORE/...), "
        "value and source (item, passive, skill, base/penalty, config). Answers 'where does my X "
        "come from?'. Resistances are fed by both e.g. ColdResist and ElementalResist.",
        {"mods": {"type": "array", "items": {"type": "string"}}}, ["mods"]),
    _fn("describe_skill",
        "The real PoE2 description and tags of a skill or support gem from PoB's game data.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("get_item_details",
        "Full text of equipped items including all mods. Omit slot for every item.",
        {"slot": {"type": "string", "description": "e.g. Helmet, Body Armour, Gloves, Boots, "
                  "Amulet, Ring 1, Ring 2, Belt, Weapon 1, Weapon 2, Flask 1"}}),
    _fn("search_passives",
        "Search passive tree nodes by name or stat text. Results are sorted by how many "
        "points away they are (pathCost).",
        {"query": {"type": "string", "description": "Substring of node name or stat text; '' for all"},
         "types": {"type": "array", "items": {"type": "string", "enum": ["Normal", "Notable", "Keystone"]}},
         "reachable_only": {"type": "boolean", "description": "Only nodes connected to the current tree"},
         "allocated": {"type": "boolean", "description": "true = only allocated, false = only unallocated"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    _fn("rank_passives",
        "Rank unallocated passives by how much each one (including its path) improves a stat, "
        "evaluated by the engine in one call. Use this for 'best nodes for X' questions instead "
        "of many compare_passives calls. Each candidate is tested alone; use compare_passives "
        "to test combinations.",
        {"stat": {"type": "string", "description": "Output key to maximise, e.g. TotalDPS, Life, "
                  "TotalEHP, ColdMaximumHitTaken (default TotalDPS)"},
         "query": {"type": "string", "description": "Optional filter on node name/stat text"},
         "types": {"type": "array", "items": {"type": "string", "enum": ["Normal", "Notable", "Keystone"]},
                   "description": "Default Notable + Keystone"},
         "max_cost": {"type": "integer", "minimum": 1, "maximum": 15, "description": "Max points incl. path (default 5)"},
         "candidates": NODE_REFS,
         "limit": {"type": "integer", "minimum": 1, "maximum": 30},
         "also": {"type": "array", "items": {"type": "string"},
                  "description": "Extra stats to report deltas for (default Life, EnergyShield, TotalEHP)"}}),
    _fn("compare_passives",
        "What-if for the passive tree (does not change the build). Adding a node includes the "
        "shortest path to it; removing a node also removes nodes that depend on it. Returns the "
        "nodes actually added/removed, pointDelta, and before/after/delta for every changed stat.",
        {"add": NODE_REFS, "remove": NODE_REFS}),
    _fn("compare_item",
        "What-if for gear (does not change the build): swap an item into a slot and return "
        "before/after/delta for every changed stat. Item text uses the in-game copy format, e.g.\n"
        "Item Class: Helmets\\nRarity: Rare\\nDoom Crown\\nHallowed Crown\\n--------\\n"
        "+80 to maximum Life\\n+40% to Cold Resistance",
        {"slot": {"type": "string"}, "item_text": {"type": "string"}}, ["slot", "item_text"]),
    _fn("list_config",
        "Search the Configuration options (combat conditions, enemy state, charges, buffs). "
        "set_only=true lists only options currently set.",
        {"query": {"type": "string"}, "set_only": {"type": "boolean"}}),
    _fn("set_config",
        "Change a Configuration option (changes the build). Use the var name from list_config; "
        "value null clears it. Returns the updated sidebar stats.",
        {"var": {"type": "string"}, "value": {"type": ["boolean", "number", "string", "null"]}},
        ["var", "value"]),
    _fn("set_main_skill",
        "Make a different skill group the main skill (changes which skill DPS stats describe). "
        "Use the 1-based group index from get_build_summary.",
        {"index": {"type": "integer", "minimum": 1}}, ["index"]),
]

# tool name -> (engine command, argument mapping)
def _engine_request(name: str, args: dict) -> tuple[str, dict]:
    if name == "get_build_summary":
        return "summary", {}
    if name == "get_stats":
        return "stats", {"keys": args.get("keys") or []}
    if name == "explain_stat":
        return "explain_stat", {"stat": args.get("stat")}
    if name == "stat_sources":
        return "stat_sources", {"mods": args.get("mods") or []}
    if name == "describe_skill":
        return "describe_skill", {"name": args.get("name")}
    if name == "get_item_details":
        return "item_details", {k: v for k, v in args.items() if k == "slot" and v}
    if name == "search_passives":
        return "search_nodes", {k: v for k, v in args.items()
                                 if k in ("query", "types", "reachable_only", "allocated", "limit")}
    if name == "rank_passives":
        return "rank_nodes", {k: v for k, v in args.items()
                              if k in ("stat", "query", "types", "max_cost", "candidates", "limit", "also") and v not in (None, "", [])}
    if name == "compare_passives":
        return "compare_nodes", {"add": args.get("add") or [], "remove": args.get("remove") or []}
    if name == "compare_item":
        return "compare_item", {"slot": args.get("slot"), "item": args.get("item_text")}
    if name == "list_config":
        return "list_config", {"query": args.get("query", ""), "set_only": bool(args.get("set_only"))}
    if name == "set_config":
        return "set_config", {"var": args.get("var"), "value": args.get("value")}
    if name == "set_main_skill":
        return "set_main_skill", {"index": args.get("index")}
    raise KeyError(name)


def _compact(value: Any) -> Any:
    """Round floats so tool results cost fewer tokens without losing meaning."""
    if isinstance(value, float):
        if value != value or abs(value) == float("inf"):
            return str(value)
        return round(value, 1) if abs(value) >= 100 else round(value, 3)
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items() if k != "tooltip"}
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


@dataclass
class ToolEvent:
    """One tool call, for showing the user what the agent did."""
    name: str
    args: dict
    ok: bool
    summary: str


@dataclass
class Agent:
    engine_call: EngineCall
    model: str = field(default_factory=lambda: settings.LLM_MODEL)
    client: AsyncOpenAI = field(default_factory=lambda: AsyncOpenAI(
        base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, timeout=120))
    messages: list[dict] = field(default_factory=lambda: [{"role": "system", "content": SYSTEM_PROMPT}])
    on_tool: Callable[[ToolEvent], None] | None = None
    on_round: Callable[[int], None] | None = None  # called before each model request (1-based)
    tokens_used: int = 0

    preload_context: bool = True

    def reset(self) -> None:
        self.messages = self.messages[:1]

    async def _build_context(self) -> str:
        """Snapshot of the build and active config, given to the model up front so it always
        knows the assumptions behind the numbers (and needs one fewer round trip)."""
        summary = await self.engine_call("summary") or {}
        config = await self.engine_call("list_config", query="", set_only=True) or {}
        snapshot = {
            "character": {k: summary.get(k) for k in
                          ("class", "ascendancy", "level", "mainSkill", "passivePoints", "ascendancyPoints")},
            "skills": [{"index": g["index"], "active": g.get("activeSkill"), "main": g["isMain"],
                        "supports": [x["name"] for x in g["gems"] if x["support"]]}
                       for g in summary.get("skills", []) if g.get("gems")],
            "items": {i["slot"]: i["name"] for i in summary.get("items", [])},
            "keystones": summary.get("keystones", []),
            "notables": summary.get("notables", []),
            "stats": {r["stat"]: r["value"] for r in (summary.get("stats") or {}).get("player", [])},
            "active_configuration": [
                {"var": o["var"], "label": o["label"], "value": o.get("value")}
                for o in (config or {}).get("options", [])
            ],
        }
        return ("Build snapshot at the start of this conversation (stats are PoB sidebar values; "
                "config lists every non-default Configuration setting):\n"
                + json.dumps(_compact(snapshot), separators=(",", ":")))

    async def _run_tool(self, name: str, raw_args: str) -> tuple[str, ToolEvent]:
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            event = ToolEvent(name, {}, False, f"bad arguments: {e}")
            return json.dumps({"error": f"invalid JSON arguments: {e}"}), event
        try:
            cmd, engine_args = _engine_request(name, args)
            result = await self.engine_call(cmd, **engine_args)
        except KeyError:
            return json.dumps({"error": f"unknown tool {name}"}), ToolEvent(name, args, False, "unknown tool")
        except EngineError as e:
            return json.dumps({"error": str(e)}), ToolEvent(name, args, False, str(e))
        text = json.dumps(_compact(result), separators=(",", ":"))
        if len(text) > MAX_TOOL_RESULT_CHARS:
            text = text[:MAX_TOOL_RESULT_CHARS] + '..."[truncated: narrow the query]"'
        return text, ToolEvent(name, args, True, f"{len(text)} chars")

    async def ask(self, question: str) -> str:
        """Answer one user message, calling engine tools as needed.

        If anything fails mid-way, the conversation is rolled back to before the
        question, so a half-finished tool exchange can't poison later requests.
        """
        checkpoint = len(self.messages)
        try:
            return await self._ask(question)
        except BaseException:
            del self.messages[checkpoint:]
            raise

    async def _ask(self, question: str) -> str:
        if self.preload_context and len(self.messages) == 1:
            self.messages.append({"role": "system", "content": await self._build_context()})
        self.messages.append({"role": "user", "content": question})
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            final_round = round_no == MAX_TOOL_ROUNDS
            if self.on_round:
                self.on_round(round_no + 1)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=TOOLS,
                # On the last round, force a written answer from what we have.
                tool_choice="none" if final_round else "auto",
                max_tokens=4096,
            )
            if response.usage:
                self.tokens_used += response.usage.total_tokens
            message = response.choices[0].message
            self.messages.append(message.model_dump(exclude_none=True))
            if not message.tool_calls:
                return message.content or "(no answer)"
            for call in message.tool_calls:
                content, event = await self._run_tool(call.function.name, call.function.arguments)
                if self.on_tool:
                    self.on_tool(event)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        return "(stopped: too many tool calls)"
