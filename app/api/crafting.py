"""Turn the engine's affix ranking (worker.lua rank_affixes) into crafting routes.

The engine says which affixes an item has, which mods could fill an open (or freed) slot
at their best tier for the item level, and what each is worth to the build. This module
adds how to get each one, from the rules in crafting_rules.py:

  - which currency applies at the item's rarity, and which omen pins it to the right side
  - what the Greater/Perfect tiers' minimum modifier level does to the odds
  - which essences guarantee the mod

Odds are counts of eligible tiers, not probabilities: PoE2's mod weights aren't in the
game files (see crafting_rules.py), so "1 of 40" means one of 40 tiers that can roll,
each assumed equally likely. Treat them as a rough ordering, never as a real chance.
"""

from __future__ import annotations

from typing import Any

from crafting_rules import CURRENCY_BY_NAME

TIER_PREFIXES = ("", "Greater ", "Perfect ")
SIDE_WORD = {"prefix": "Sinistral", "suffix": "Dextral"}
OTHER_SIDE = {"prefix": "suffix", "suffix": "prefix"}

UNWEIGHTED_NOTE = ("Odds count eligible mod tiers as equally likely; PoE2's real mod weights "
                   "aren't in the game data, so treat them as a rough ordering only.")


def _tier_count(pool: list[dict], sides: set[str], floor: int, group: str | None = None) -> int:
    return sum(1 for entry in pool if entry["side"] in sides and (group is None or entry["group"] == group)
               for level in entry["levels"] if level >= floor)


def _roll_routes(currency: str, cand: dict, pool: list[dict], sides: set[str],
                 omens: list[str], note: str = "") -> list[dict]:
    """One route per tier (plain/Greater/Perfect) of a random-add currency that can still
    hit the candidate's group. A higher floor shrinks the pool but may exclude the group."""
    routes = []
    for prefix in TIER_PREFIXES:
        rule = CURRENCY_BY_NAME[prefix + currency]
        hits = _tier_count(pool, {cand["side"]}, rule.min_mod_level, cand["group"])
        if not hits:
            continue
        total = _tier_count(pool, sides, rule.min_mod_level)
        routes.append({
            "steps": [rule.name], "omens": omens, "guaranteed": False,
            "hits": hits, "outOf": total, "minModLevel": rule.min_mod_level or None,
            "note": note or rule.text,
        })
    return routes


def _essence_routes(cand: dict, rarity: str) -> list[dict]:
    routes = []
    for ess in cand.get("essences") or []:
        replaces = ess["name"].startswith(("Perfect ", "Essence of Hysteria", "Essence of Delirium",
                                           "Essence of Horror", "Essence of Insanity",
                                           "Essence of the Abyss", "Essence of the Breach"))
        if replaces:
            if rarity != "rare":
                continue
            side = SIDE_WORD[cand["side"]]
            routes.append({
                "steps": [ess["name"]], "omens": [], "guaranteed": True, "gives": ess.get("text"),
                "note": "Removes a random modifier first; Omen of "
                        f"{'Dextral' if side == 'Sinistral' else 'Sinistral'} Crystallisation "
                        "limits the removal to the other side.",
            })
        else:
            before = {"normal": ["Orb of Transmutation"], "magic": [], "rare": None}[rarity]
            if before is None:
                routes.append({"steps": [ess["name"]], "omens": [], "guaranteed": True,
                               "gives": ess.get("text"), "needsNewBase": True,
                               "note": "Lesser/normal/Greater essences only work on Magic items: "
                                       "start a new base of the same type."})
            else:
                routes.append({"steps": before + [ess["name"]], "omens": [], "guaranteed": True,
                               "gives": ess.get("text"),
                               "note": "Upgrades Magic to Rare with this mod guaranteed."})
    return routes


def _routes(cand: dict, result: dict) -> list[dict]:
    rarity = result["rarity"]
    side, other = cand["side"], OTHER_SIDE[cand["side"]]
    pool = result["pool"]
    other_open = result["open"][other] > 0
    routes: list[dict] = []
    if rarity == "rare":
        # A side omen is only needed when the other side also has room.
        omens = [f"Omen of {SIDE_WORD[side]} Exaltation"] if other_open else []
        sides = {side} if omens or not other_open else {side, other}
        add = _roll_routes("Exalted Orb", cand, pool, sides, omens)
        rep = result.get("replacing")
        if rep:
            same_side = [c for c in result["current"] if c["side"] == side and not c.get("fractured")]
            annul = {
                "steps": ["Orb of Annulment"], "omens": [f"Omen of {SIDE_WORD[side]} Annulment"],
                "guaranteed": False, "hits": 1, "outOf": len(same_side),
                "note": f"Removes one random {side}; you want it to take {rep['group']}.",
            }
            routes += [{**r, "steps": annul["steps"] + r["steps"], "omens": annul["omens"] + r["omens"],
                        "removal": annul} for r in add]
            levels = [c["level"] for c in result["current"] if c.get("level") and not c.get("fractured")]
            if levels and rep.get("level") == min(levels) and levels.count(min(levels)) == 1:
                for r in _roll_routes("Chaos Orb", cand, pool, {side, other}, ["Omen of Whittling"],
                                      "Removes the lowest-level modifier (the one you're replacing) "
                                      "and adds a random one on either side."):
                    routes.append(r)
        else:
            routes += add
    elif rarity == "magic":
        if result["open"][side] > 0:
            routes += _roll_routes("Orb of Augmentation", cand, pool, {side}, [])
        omens = [f"Omen of {SIDE_WORD[side]} Coronation"]
        routes += _roll_routes("Regal Orb", cand, pool, {side}, omens,
                               "Upgrades to Rare, adding this side's mod.")
    elif rarity == "normal":
        routes += _roll_routes("Orb of Transmutation", cand, pool, {"prefix", "suffix"}, [])
    routes += _essence_routes(cand, rarity)
    return routes


def plan(result: dict[str, Any]) -> dict[str, Any]:
    """Attach routes to each candidate. Drops the raw pool (it's only needed for odds)."""
    blocked = None
    if result.get("corrupted"):
        blocked = "Corrupted: normal crafting currency can't change this item. Results show what a re-crafted copy would gain."
    elif result.get("mirrored"):
        blocked = "Mirrored: the item can't be modified. Results show what a re-crafted copy would gain."
    for cand in result.get("candidates", []):
        cand["routes"] = [] if blocked else _routes(cand, result)
    pool = result.pop("pool", [])
    result["poolTiers"] = {s: _tier_count(pool, {s}, 0) for s in ("prefix", "suffix")}
    result["blocked"] = blocked
    result["oddsNote"] = UNWEIGHTED_NOTE
    return result
