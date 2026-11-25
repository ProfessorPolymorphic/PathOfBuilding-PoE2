"""HTTP API over the headless Path of Building engine.

Each loaded build is a session backed by its own engine worker process. Workers
are capped (POB_MAX_WORKERS); when the cap is hit the least-recently-used worker
is shut down, but its build XML is kept, so the next request to that build
transparently restarts a worker and reloads it (~3s).

Run:  app/api/.venv/bin/uvicorn main:app --app-dir app/api --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field, model_validator

import buildcode
import crafting
import distribution
import settings
import snapshots
from agent import Agent, ToolEvent
from engine import MUTATING, EngineCrashed, EngineError, EngineWorker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pob.api")

MAX_WORKERS = int(os.getenv("POB_MAX_WORKERS", "4"))
# Loaded builds are saved here as XML so they survive server restarts and rebuilds.
STATE_DIR = Path(os.getenv("POB_STATE_DIR", Path(__file__).resolve().parent.parent / ".state" / "builds"))
BUILD_ID_RE = re.compile(r"^[0-9a-f]{12}$")
# Every pasted build is also kept here, unchanged, as history (see snapshots.py).
SNAPSHOT_DIR = Path(os.getenv("POB_SNAPSHOT_DIR", STATE_DIR.parent / "snapshots"))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, build_id: str):
        self.id = build_id
        self.worker = EngineWorker()
        self.last_used = time.monotonic()
        self.agent: Agent | None = None  # created on first chat message
        self.chat_lock = asyncio.Lock()  # one question at a time per build
        # Dashboard data for the build as it was when computed: (xml it describes, data).
        self.dashboard: tuple[str | None, dict] | None = None
        self.dashboard_lock = asyncio.Lock()


class SessionManager:
    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    def get(self, build_id: str) -> Session:
        session = self.sessions.get(build_id) or self._restore(build_id)
        if not session:
            raise HTTPException(404, f"no build with id {build_id}")
        session.last_used = time.monotonic()
        return session

    # -- persistence: one XML (+ name) file per build; workers reload it on demand --

    def _paths(self, build_id: str) -> tuple[Path, Path]:
        return STATE_DIR / f"{build_id}.xml", STATE_DIR / f"{build_id}.json"

    def _persist(self, session: Session) -> None:
        if not session.worker.xml_snapshot:
            return
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            xml_path, meta_path = self._paths(session.id)
            tmp = xml_path.with_suffix(".tmp")
            tmp.write_text(session.worker.xml_snapshot)
            tmp.replace(xml_path)  # atomic: never leave a half-written build
            meta_path.write_text(json.dumps({"name": session.worker.build_name}))
        except OSError as e:
            log.warning("could not save build %s: %s", session.id, e)

    def _restore(self, build_id: str) -> Session | None:
        """Bring back a build saved before a restart. Its worker starts on first use."""
        if not BUILD_ID_RE.match(build_id):
            return None
        xml_path, meta_path = self._paths(build_id)
        if not xml_path.exists():
            return None
        session = Session(build_id)
        session.worker.xml_snapshot = xml_path.read_text()
        try:
            session.worker.build_name = json.loads(meta_path.read_text()).get("name") or "build"
        except (OSError, ValueError):
            pass
        self.sessions[build_id] = session
        log.info("restored build %s from disk", build_id)
        return session

    async def create(self) -> Session:
        session = Session(uuid.uuid4().hex[:12])
        self.sessions[session.id] = session
        return session

    async def make_room(self, keep: Session) -> None:
        """Shut down LRU workers (keeping their XML) so at most max_workers run."""
        async with self._lock:
            live = [s for s in self.sessions.values() if s.worker.alive and s is not keep]
            excess = len(live) + 1 - self.max_workers
            for s in sorted(live, key=lambda s: s.last_used)[:max(excess, 0)]:
                log.info("parking worker for build %s", s.id)
                await s.worker.close()

    async def call(self, session: Session, cmd: str, **args: Any) -> Any:
        if not session.worker.alive:
            await self.make_room(keep=session)
        result = await session.worker.call(cmd, **args)
        if cmd in MUTATING:
            self._persist(session)
        return result

    async def delete(self, build_id: str) -> None:
        session = self.sessions.pop(build_id, None)
        if session:
            await session.worker.close()
        for path in self._paths(build_id):
            path.unlink(missing_ok=True)

    async def close_all(self) -> None:
        await asyncio.gather(*(s.worker.close() for s in self.sessions.values()))


sessions = SessionManager(MAX_WORKERS)
history = snapshots.SnapshotStore(SNAPSHOT_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await sessions.close_all()


app = FastAPI(title="PoB Engine API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(EngineError)
async def engine_error(_: Request, exc: EngineError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(EngineCrashed)
async def engine_crashed(_: Request, exc: EngineCrashed):
    log.error("engine crashed: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "engine crashed; retry the request"})


@app.exception_handler(settings.LLMNotConfigured)
async def llm_not_configured(_: Request, exc: settings.LLMNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(buildcode.InvalidBuildCode)
async def bad_code(_: Request, exc: buildcode.InvalidBuildCode):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class LoadBuild(BaseModel):
    code: str | None = Field(None, description="PoB share code")
    xml: str | None = Field(None, description="Raw PoB build XML")
    name: str | None = None
    character: str | None = Field(None, max_length=60,
                                  description="Groups snapshots; defaults to the character's class")

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.code) == bool(self.xml):
            raise ValueError("provide exactly one of 'code' or 'xml'")
        return self


class NodeChange(BaseModel):
    add: list[str | int] = []
    remove: list[str | int] = []


class ItemChange(BaseModel):
    slot: str = Field(..., examples=["Helmet", "Ring 1", "Weapon 1"])
    item: str = Field(..., description="Item text as copied in-game (Ctrl+C)")


class ConfigValue(BaseModel):
    value: bool | int | float | str | None


class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def make_agent(session: Session) -> Agent:
    """Agent whose engine calls go through the session manager (so parked workers revive)."""
    settings.require_llm_configured()

    async def engine_call(cmd: str, **args: Any) -> Any:
        return await sessions.call(session, cmd, **args)

    return Agent(engine_call=engine_call)


class MainSkill(BaseModel):
    index: int = Field(..., ge=1, description="Skill group index from the summary")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "ok": True,
        "chat_configured": not settings.missing_llm_settings(),
        "builds": len(sessions.sessions),
        "running_workers": sum(s.worker.alive for s in sessions.sessions.values()),
        "max_workers": MAX_WORKERS,
    }


@app.post("/builds", status_code=201)
async def load_build(body: LoadBuild):
    text = body.code or ""
    xml = body.xml or (text if buildcode.looks_like_xml(text) else buildcode.decode(text))
    session = await sessions.create()
    try:
        summary = await sessions.call(session, "load_xml", xml=xml, name=body.name or "build")
    except Exception:
        await sessions.delete(session.id)
        raise
    return {"build_id": session.id, "summary": summary, "snapshot": _save_snapshot(xml, summary, body.character)}


def _save_snapshot(xml: str, summary: dict, character: str | None) -> dict | None:
    """Keep the pasted build as history. A failure here is logged, not raised: losing one
    snapshot is better than refusing to load the build."""
    try:
        meta, created = history.add(xml, summary, character)
    except OSError as e:
        log.error("could not save snapshot: %s", e)
        return None
    return {"id": meta["id"], "character": meta["character"], "taken_at": meta["taken_at"], "created": created}


@app.get("/builds")
async def list_builds():
    return [
        {"build_id": s.id, "name": s.worker.build_name, "running": s.worker.alive}
        for s in sessions.sessions.values()
    ]


@app.get("/builds/{build_id}")
async def summary(build_id: str):
    return await sessions.call(sessions.get(build_id), "summary")


@app.delete("/builds/{build_id}", status_code=204)
async def delete_build(build_id: str):
    sessions.get(build_id)
    await sessions.delete(build_id)


@app.get("/builds/{build_id}/stats")
async def stats(build_id: str, keys: list[str] | None = Query(None, description="Raw output keys, e.g. Life")):
    return await sessions.call(sessions.get(build_id), "stats", **({"keys": keys} if keys else {}))


@app.get("/builds/{build_id}/export")
async def export(build_id: str, include_xml: bool = False):
    result = await sessions.call(sessions.get(build_id), "export_xml")
    out = {"code": buildcode.encode(result["xml"])}
    if include_xml:
        out["xml"] = result["xml"]
    return out


@app.get("/builds/{build_id}/card")
async def card(build_id: str):
    """The change preview card's core stats for the build as it is."""
    return await sessions.call(sessions.get(build_id), "card")


# Reachable passives tested for the dashboard: every notable/keystone within this many points.
DASHBOARD_MAX_COST = 5


async def _dashboard(session: Session) -> dict:
    """Baseline card plus every reachable notable/keystone tested on its own, each with its
    change preview card. One rank_nodes pass (~4s) serves both the damage and defence lists;
    the result is cached until the build changes."""
    async with session.dashboard_lock:
        if session.dashboard and session.dashboard[0] == session.worker.xml_snapshot \
                and session.worker.xml_snapshot is not None:
            return session.dashboard[1]
        summary = await sessions.call(session, "summary")
        baseline = await sessions.call(session, "card")
        ranked = await sessions.call(session, "rank_nodes", stat="DPS", max_cost=DASHBOARD_MAX_COST,
                                     limit=300, cards=True, also=["TotalEHP"])
        nodes = [{
            "id": r["id"], "name": r["name"], "type": r["type"], "cost": r["cost"],
            "path": r.get("path") or [], "stats": r.get("stats") or [], "card": r["card"],
        } for r in ranked["results"]]
        data = {
            "build": {k: summary.get(k) for k in ("name", "class", "ascendancy", "level", "mainSkill")},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "baseline": baseline,
            "nodes": {
                "max_cost": DASHBOARD_MAX_COST,
                "types": ["Notable", "Keystone"],
                "evaluated": ranked["evaluated"],
                "candidates": nodes,
            },
        }
        session.dashboard = (session.worker.xml_snapshot, data)
        return data


@app.get("/builds/{build_id}/dashboard")
async def dashboard(build_id: str, download: bool = False):
    """Everything the dashboard shows, straight from the engine. With download=true it comes
    as a file, for sketching views against real data (e.g. in Observable)."""
    data = await _dashboard(sessions.get(build_id))
    if not download:
        return data
    b = data["build"]
    name = re.sub(r"[^A-Za-z0-9]+", "-", f"{b.get('ascendancy') or b.get('class')}-{b.get('level')}").strip("-")
    return JSONResponse(data, headers={
        "Content-Disposition": f'attachment; filename="dashboard-{name.lower()}.json"'})


class Scenario(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    config: dict[str, bool | int | float | str | None] = Field(
        default_factory=dict, description="Configuration overrides for this scenario only")


class DistributionRequest(BaseModel):
    skill: str | int = Field(..., description="Skill group index, or the name of its active gem")
    scenarios: list[Scenario] = Field(
        default_factory=lambda: [Scenario(name="As configured")], min_length=1, max_length=6)
    samples: int = Field(20000, ge=100, le=200000)
    seed: int = 1
    by_type: bool = Field(True, description="Include each sample's damage split by type")


@app.post("/builds/{build_id}/distribution")
async def hit_distribution(build_id: str, body: DistributionRequest):
    """Damage of one attack or cast as a distribution: every random draw PoB averages (hit,
    crit, per-type damage rolls, lucky rolls, double/triple damage), sampled. Each scenario
    applies its config overrides for this calculation only; the build isn't changed."""
    session = sessions.get(build_id)
    out = []
    for sc in body.scenarios:
        result = await sessions.call(session, "hit_distribution", group=body.skill, config=sc.config)
        if not result["hands"]:
            raise HTTPException(422, f"{result.get('skill') or body.skill} doesn't deal hit damage")
        samples = distribution.sample(result["hands"], body.samples, body.seed)
        if not body.by_type:
            del samples["by_type"]
        out.append({
            "name": sc.name,
            "config": sc.config,
            "pob": result["pob"],
            "hands": result["hands"],
            "summary": distribution.summarize(samples, result["hands"]),
            "samples": samples,
        })
    return {"skill": result["skill"], "group": result["group"], "samples": body.samples, "scenarios": out}


class SweepRequest(BaseModel):
    skill: str | int = Field(..., description="Skill group index, or the name of its active gem")
    defence: str = Field(..., description="Enemy defence config var, e.g. enemyArmour")
    lo: float | None = Field(None, description="Lowest value (default: the defence's usual range)")
    hi: float | None = None
    steps: int = Field(25, ge=3, le=50)
    samples: int = Field(5000, ge=200, le=50000, description="Sampled attacks per step")
    bins: int = Field(160, ge=20, le=400)
    seed: int = 1


@app.get("/defences")
async def list_defences():
    """Enemy defences a sweep can vary, with labels and default ranges."""
    return [{"var": k, **{**v, "range": list(v["range"])}} for k, v in distribution.DEFENCES.items()]


@app.post("/builds/{build_id}/sweep")
async def hit_sweep(build_id: str, body: SweepRequest):
    """A skill's damage distribution at each value of one enemy defence (armour, evasion, a
    resistance, ...), everything else as configured. Each step is PoB's own calculation at
    that value, sampled and binned on one shared damage axis. The build isn't changed."""
    meta = distribution.DEFENCES.get(body.defence)
    if not meta:
        raise HTTPException(422, f"can't sweep {body.defence}; see GET /defences")
    session = sessions.get(build_id)
    current = (await sessions.call(session, "sweep_current", var=body.defence))["current"]
    lo = meta["range"][0] if body.lo is None else body.lo
    hi = meta["range"][1] if body.hi is None else body.hi
    if hi <= lo:
        raise HTTPException(422, "hi must be greater than lo")
    values, grid_values = distribution.sweep_values(lo, hi, body.steps, current)
    result = await sessions.call(session, "hit_sweep", group=body.skill, var=body.defence, values=values)
    if not any(s["hands"] for s in result["steps"]):
        raise HTTPException(422, f"{result.get('skill') or body.skill} doesn't deal hit damage")
    grid = distribution.sweep_grid(result["steps"], body.samples, body.bins, body.seed, grid_values)
    return {"skill": result["skill"], "group": result["group"], "defence": body.defence,
            "label": meta["label"], "unit": meta["unit"], "affects": meta["affects"],
            "current": current, "lo": lo, "hi": hi, "samples": body.samples, **grid}


def weak_spot_flags(data: dict) -> dict:
    """Relative reading of max hit by type: no outside thresholds, only the build against itself.
    Each type gets its share of the strongest type's max hit; the weakest is flagged, and so is
    every resistance below its cap (with the exact max hit if it were capped)."""
    types = [t for t in data["types"] if t.get("maxHit")]
    best = max((t["maxHit"] for t in types), default=0) or 1
    weakest = min(types, key=lambda t: t["maxHit"])["type"] if types else None
    for t in data["types"]:
        hit = t.get("maxHit") or 0
        t["shareOfBest"] = hit / best
        t["weakest"] = t["type"] == weakest
        t["uncapped"] = bool(t.get("missing"))
        if t.get("cappedMaxHit") is not None:
            t["gainIfCapped"] = t["cappedMaxHit"] - hit
    data["weakest"] = weakest
    return data


@app.get("/builds/{build_id}/weak-spots")
async def weak_spots(build_id: str, download: bool = False):
    """Max hit by damage type, the pool and resistances behind it, which type is weakest, and
    for every uncapped resistance the max hit if it were capped (computed by the engine)."""
    data = weak_spot_flags(await sessions.call(sessions.get(build_id), "weak_spots"))
    if download:
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="weak-spots-{build_id}.json"'})
    return data


@app.put("/builds/{build_id}/main-skill")
async def set_main_skill(build_id: str, body: MainSkill):
    return await sessions.call(sessions.get(build_id), "set_main_skill", index=body.index)


# -- passive tree --

@app.get("/builds/{build_id}/nodes")
async def search_nodes(
    build_id: str,
    query: str = "",
    types: list[str] | None = Query(None, description="Normal, Notable, Keystone, Mastery, Socket"),
    reachable_only: bool = False,
    allocated: bool | None = None,
    limit: int = Query(25, ge=1, le=500),
):
    args: dict[str, Any] = {"query": query, "reachable_only": reachable_only, "limit": limit}
    if types:
        args["types"] = types
    if allocated is not None:
        args["allocated"] = allocated
    return await sessions.call(sessions.get(build_id), "search_nodes", **args)


class RankNodes(BaseModel):
    stat: str = "TotalDPS"
    query: str = ""
    types: list[str] | None = None
    max_cost: int = Field(5, ge=1, le=15)
    candidates: list[str | int] | None = None
    limit: int = Field(10, ge=1, le=100)
    also: list[str] | None = None
    cards: bool = Field(False, description="Attach a change preview card to each result")


@app.post("/builds/{build_id}/nodes/rank")
async def rank_nodes(build_id: str, body: RankNodes):
    args = {k: v for k, v in body.model_dump().items() if v not in (None, "", [])}
    return await sessions.call(sessions.get(build_id), "rank_nodes", **args)


@app.post("/builds/{build_id}/nodes/compare")
async def compare_nodes(build_id: str, body: NodeChange):
    return await sessions.call(sessions.get(build_id), "compare_nodes", add=body.add, remove=body.remove)


@app.post("/builds/{build_id}/nodes/apply")
async def apply_nodes(build_id: str, body: NodeChange):
    return await sessions.call(sessions.get(build_id), "apply_nodes", add=body.add, remove=body.remove)


# -- items --

@app.get("/builds/{build_id}/items")
async def item_details(build_id: str, slot: str | None = None):
    return await sessions.call(sessions.get(build_id), "item_details", **({"slot": slot} if slot else {}))


@app.post("/builds/{build_id}/items/compare")
async def compare_item(build_id: str, body: ItemChange):
    return await sessions.call(sessions.get(build_id), "compare_item", slot=body.slot, item=body.item)


class RankAffixes(BaseModel):
    slot: str
    stat: str = Field("DPS", description="Output key to rank by; DPS = the main skill's total")
    also: list[str] | None = None
    roll: float = Field(0.5, ge=0, le=1, description="Where in each mod's range to evaluate")
    limit: int = Field(15, ge=1, le=100)
    replace: str | None = Field(None, description="Group or mod id of a current affix to replace")


@app.post("/builds/{build_id}/items/affixes")
async def rank_affixes(build_id: str, body: RankAffixes):
    """The item's affixes, and what each mod that could fill an open (or replaced) slot is
    worth to the build, with the currency routes to get it."""
    args = {k: v for k, v in body.model_dump().items() if v not in (None, "", [])}
    return crafting.plan(await sessions.call(sessions.get(build_id), "rank_affixes", **args))


@app.post("/builds/{build_id}/items/equip")
async def equip_item(build_id: str, body: ItemChange):
    return await sessions.call(sessions.get(build_id), "equip_item", slot=body.slot, item=body.item)


# -- configuration --

@app.get("/builds/{build_id}/config")
async def list_config(build_id: str, query: str = "", set_only: bool = False):
    return await sessions.call(sessions.get(build_id), "list_config", query=query, set_only=set_only)


@app.put("/builds/{build_id}/config/{var}")
async def set_config(build_id: str, var: str, body: ConfigValue):
    return await sessions.call(sessions.get(build_id), "set_config", var=var, value=body.value)


@app.get("/builds/{build_id}/sustain")
async def sustain(build_id: str):
    """Every enabled skill's cost, use rate and mana sustain (spend vs regen vs leech/on-hit,
    net per second, seconds until empty), each calculated as if it were the main skill."""
    return await sessions.call(sessions.get(build_id), "skill_costs")


class GemCompare(BaseModel):
    group: str | int = Field(..., description="Skill name or 1-based group index")
    remove: list[str] = []
    add: list[str] = []


@app.post("/builds/{build_id}/compare/gems")
async def compare_gems(build_id: str, body: GemCompare):
    """What-if for one skill's supports (the build is unchanged): damage, cost and mana
    sustain before/after, plus a change preview card."""
    return await sessions.call(sessions.get(build_id), "compare_gems",
                               group=body.group, remove=body.remove, add=body.add)


@app.get("/builds/{build_id}/supports")
async def list_supports(build_id: str, group: str | None = None):
    """Support gem names; with `group`, only those that can support that group's skill."""
    args = {"group": int(group) if group and group.isdigit() else group} if group else {}
    return await sessions.call(sessions.get(build_id), "list_supports", **args)


# -- diagnostics --

@app.post("/builds/{build_id}/benchmark")
async def benchmark(build_id: str, samples: int = Query(50, ge=1, le=1000)):
    return await sessions.call(sessions.get(build_id), "benchmark", samples=samples)


# -- chat --

@app.post("/builds/{build_id}/chat")
async def chat(build_id: str, body: ChatMessage):
    """Ask the build agent a question. The conversation continues until reset."""
    session = sessions.get(build_id)
    async with session.chat_lock:
        if session.agent is None:
            session.agent = make_agent(session)
        agent = session.agent
        events: list[ToolEvent] = []
        agent.on_tool = events.append
        tokens_before = agent.tokens_used
        try:
            answer = await agent.ask(body.message)
        except (EngineError, EngineCrashed):
            raise
        except Exception as e:  # model endpoint down, VPN off, quota exhausted...
            log.warning("LLM request failed: %s", e)
            raise HTTPException(502, f"language model request failed: {e}") from e
        return {
            "answer": answer,
            "tool_calls": [_tool_json(e) for e in events],
            "tokens": agent.tokens_used - tokens_before,
            "model": agent.model,
        }


def _tool_json(e: ToolEvent) -> dict:
    out = {"name": e.name, "args": e.args, "ok": e.ok, "summary": e.summary}
    if e.card:
        out["card"] = e.card
        out["card_title"] = e.card_title
        if e.card_detail:
            out["card_detail"] = e.card_detail
    return out


@app.post("/builds/{build_id}/chat/stream")
async def chat_stream(build_id: str, body: ChatMessage):
    """Like /chat, but streams progress as server-sent events while the agent works:
    `thinking` (a model request started), `tool` (an engine call finished),
    then exactly one of `answer` or `error`."""
    session = sessions.get(build_id)
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def run() -> None:
        async with session.chat_lock:
            try:
                if session.agent is None:
                    session.agent = make_agent(session)
                agent = session.agent
                tokens_before = agent.tokens_used
                tools: list[ToolEvent] = []

                def on_tool(e: ToolEvent) -> None:
                    tools.append(e)
                    queue.put_nowait(("tool", _tool_json(e)))

                agent.on_tool = on_tool
                agent.on_round = lambda n: queue.put_nowait(("thinking", {"round": n}))
                try:
                    answer = await agent.ask(body.message)
                finally:
                    agent.on_tool = agent.on_round = None
                queue.put_nowait(("answer", {
                    "answer": answer,
                    "tool_calls": [_tool_json(e) for e in tools],
                    "tokens": agent.tokens_used - tokens_before,
                    "model": agent.model,
                }))
            except Exception as e:  # LLM down / not configured / engine crash
                log.warning("chat failed: %s", e)
                detail = str(e) if isinstance(e, (EngineError, settings.LLMNotConfigured)) \
                    else f"language model request failed: {e}"
                queue.put_nowait(("error", {"detail": detail}))
            finally:
                queue.put_nowait(None)

    # The agent runs to completion even if the browser disconnects, so the
    # conversation history stays consistent.
    task = asyncio.create_task(run())

    async def events():
        while (item := await queue.get()) is not None:
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
        await task

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/builds/{build_id}/chat", status_code=204)
async def reset_chat(build_id: str):
    """Start a fresh conversation (build changes made via set_config are kept)."""
    session = sessions.get(build_id)
    async with session.chat_lock:
        if session.agent:
            session.agent.reset()


# -- snapshots: history of pasted builds --

class SnapshotUpdate(BaseModel):
    character: str = Field(..., min_length=1, max_length=60)


def _snapshot(snap_id: str) -> dict:
    if not (meta := history.get(snap_id)):
        raise HTTPException(404, f"no snapshot with id {snap_id}")
    return meta


@app.get("/characters")
async def list_characters():
    """Characters with saved snapshots, most recently pasted first."""
    return history.characters()


@app.get("/snapshots")
async def list_snapshots(character: str | None = None):
    """Snapshots oldest first, each with its sidebar stats as {stat: value}."""
    return [snapshots.listing(m) for m in history.list(character)]


@app.get("/snapshots/{snap_id}")
async def get_snapshot(snap_id: str, include_xml: bool = False):
    meta = _snapshot(snap_id)
    if include_xml:
        meta = {**meta, "xml": history.xml(snap_id)}
    return meta


@app.patch("/snapshots/{snap_id}")
async def update_snapshot(snap_id: str, body: SnapshotUpdate):
    _snapshot(snap_id)
    return snapshots.listing(history.rename(snap_id, body.character))


@app.delete("/snapshots/{snap_id}", status_code=204)
async def delete_snapshot(snap_id: str):
    _snapshot(snap_id)
    history.delete(snap_id)


@app.post("/snapshots/{snap_id}/load", status_code=201)
async def load_snapshot(snap_id: str):
    """Open a saved snapshot as a working build (doesn't record a new snapshot)."""
    meta = _snapshot(snap_id)
    xml = history.xml(snap_id)
    if xml is None:
        raise HTTPException(404, f"snapshot {snap_id} has no saved build")
    session = await sessions.create()
    try:
        summary = await sessions.call(session, "load_xml", xml=xml, name=meta["character"])
    except Exception:
        await sessions.delete(session.id)
        raise
    return {"build_id": session.id, "summary": summary}


# -- web app --
# Serve the built React app (app/web/dist) at / when it exists, e.g. in the Docker image.
# Mounted last so every API route above takes precedence. In development, Vite serves it.
WEB_DIST = Path(os.getenv("WEB_DIST", Path(__file__).resolve().parent.parent / "web" / "dist"))
if (WEB_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
