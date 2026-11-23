"""Sample whole hits from the roll model the engine extracts (worker.lua hit_distribution).

PoB reports one average per hit. The engine hands back the random draws that average is
made of; this module draws them, so a hit's damage can be shown as a distribution. Every
parameter comes from the engine; nothing here invents a mechanic. The model's mean is
PoB's own AverageDamage (see `model_mean`), which the tests check.

Draw order for one attempt, following PoB's calculation:
  1. hand: main or off hand, equally likely when both attack (they alternate)
  2. hit or miss (HitChance)
  3. crit or not, given a hit (PoB's effective CritChance)
  4. each damage type rolls uniformly in its [min, max] for crits or non-crits, taking
     the better of two rolls with its `lucky` chance
  5. double or triple damage (exclusive), else normal
"""

from __future__ import annotations

import random
from typing import Any

QUANTILES = (0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99)


def _side_mean(model: dict, side: str) -> float:
    total = 0.0
    for t in model["types"]:
        r = t.get(side)
        if r:
            total += (r["min"] + r["max"]) / 2 + r["lucky"] * (r["max"] - r["min"]) / 6
    return total


def model_mean(model: dict) -> dict[str, float]:
    """Exact expected damage of one hand's model: per hit and per attempt (misses count 0)."""
    mult = 1 + model["doubleDamageChance"] + 2 * model["tripleDamageChance"]
    crit = model["critChance"]
    per_hit = (_side_mean(model, "hit") * (1 - crit) + _side_mean(model, "crit") * crit) * mult
    return {"per_hit": per_hit, "per_attempt": per_hit * model["hitChance"]}


def _roll(rng: random.Random, r: dict) -> float:
    u = rng.random()
    if r["lucky"] > 0 and rng.random() < r["lucky"]:
        u = max(u, rng.random())
    return r["min"] + (r["max"] - r["min"]) * u


def sample(hands: list[dict], n: int, seed: int = 1) -> dict[str, list]:
    """n attempts as columns: damage (0 for a miss), outcome (miss/hit/crit), multiplier (1-3),
    hand index, and damage by type."""
    rng = random.Random(seed)
    models = [h["model"] for h in hands]
    type_names = sorted({t["type"] for m in models for t in m["types"]})
    damage: list[float] = []
    outcome: list[str] = []
    multiplier: list[int] = []
    hand_idx: list[int] = []
    by_type: dict[str, list[float]] = {t: [] for t in type_names}
    for _ in range(n):
        h = rng.randrange(len(models)) if len(models) > 1 else 0
        m = models[h]
        hand_idx.append(h)
        if rng.random() >= m["hitChance"]:
            outcome.append("miss")
            multiplier.append(1)
            damage.append(0.0)
            for t in type_names:
                by_type[t].append(0.0)
            continue
        side = "crit" if rng.random() < m["critChance"] else "hit"
        u = rng.random()
        mult = 3 if u < m["tripleDamageChance"] else 2 if u < m["tripleDamageChance"] + m["doubleDamageChance"] else 1
        rolled = {t: 0.0 for t in type_names}
        for t in m["types"]:
            r = t.get(side)
            if r:
                rolled[t["type"]] = _roll(rng, r) * mult
        outcome.append(side)
        multiplier.append(mult)
        damage.append(round(sum(rolled.values()), 1))
        for t in type_names:
            by_type[t].append(round(rolled[t], 1))
    return {"damage": damage, "outcome": outcome, "multiplier": multiplier, "hand": hand_idx,
            "by_type": by_type}


def summarize(samples: dict[str, list], hands: list[dict]) -> dict[str, Any]:
    dmg = sorted(samples["damage"])
    n = len(dmg)
    means = [model_mean(h["model"]) for h in hands]
    counts = {k: samples["outcome"].count(k) for k in ("miss", "hit", "crit")}
    return {
        "n": n,
        # Exact, from the model (equal to PoB's averages); the sample mean wobbles around it.
        "mean_per_attempt": sum(m["per_attempt"] for m in means) / len(means),
        "mean_per_hit": sum(m["per_hit"] for m in means) / len(means),
        "sample_mean": sum(dmg) / n if n else 0,
        "quantiles": {f"p{round(q * 100)}": dmg[min(n - 1, int(q * n))] for q in QUANTILES} if n else {},
        "min": dmg[0] if n else 0,
        "max": dmg[-1] if n else 0,
        "share": {k: v / n for k, v in counts.items()} if n else {},
    }


# -- sweeps: the distribution across one enemy defence ------------------------------------

# Enemy defences a sweep can vary (worker.lua SWEEP_VARS), with a default range.
# `affects` is the damage type the defence acts on, or None when it acts on every hit.
DEFENCES: dict[str, dict[str, Any]] = {
    "enemyArmour": {"label": "Enemy armour", "unit": "", "range": (0, 30000), "affects": "Physical"},
    "enemyEvasion": {"label": "Enemy evasion", "unit": "", "range": (0, 12000), "affects": None},
    "enemyPhysicalReduction": {"label": "Enemy physical damage reduction", "unit": "%", "range": (0, 90), "affects": "Physical"},
    "enemyFireResist": {"label": "Enemy fire resistance", "unit": "%", "range": (-60, 75), "affects": "Fire"},
    "enemyColdResist": {"label": "Enemy cold resistance", "unit": "%", "range": (-60, 75), "affects": "Cold"},
    "enemyLightningResist": {"label": "Enemy lightning resistance", "unit": "%", "range": (-60, 75), "affects": "Lightning"},
    "enemyChaosResist": {"label": "Enemy chaos resistance", "unit": "%", "range": (-60, 75), "affects": "Chaos"},
    "enemyBlockChance": {"label": "Enemy block chance", "unit": "%", "range": (0, 75), "affects": None},
    "enemyLevel": {"label": "Enemy level", "unit": "", "range": (1, 85), "affects": None},
}


def sweep_values(lo: float, hi: float, steps: int, current: float | None) -> tuple[list[int], set[int]]:
    """`steps` evenly spaced values from lo to hi (the grid), plus the current value so the
    build's own setting is always one of the rows. Whole numbers: PoB's config takes integers."""
    grid = {round(lo + (hi - lo) * i / (steps - 1)) for i in range(steps)}
    vals = set(grid)
    if current is not None and lo <= current <= hi:
        vals.add(round(current))
    return sorted(vals), grid


def sweep_grid(steps: list[dict], n: int, bins: int, seed: int = 1, grid: set | None = None) -> dict[str, Any]:
    """Sample every step and bin its hits on one shared damage axis.

    Each step gets, per bin, the share of its attempts that were non-crit or crit hits in
    that bin (misses are left out of the bins and reported as `miss`), so a step's bins plus
    its miss share sum to 1. Steps without hits (the skill stops hitting) have empty bins.
    `on_grid` is false for the extra row at the current value, which charts that need evenly
    spaced rows leave out.
    """
    sampled = [sample(s["hands"], n, seed) if s["hands"] else None for s in steps]
    top = max((max(x["damage"]) for x in sampled if x), default=0.0) or 1.0
    width = top * 1.02 / bins
    out = []
    for s, x in zip(steps, sampled):
        hit = [0.0] * bins
        crit = [0.0] * bins
        summary = None
        if x:
            for d, o in zip(x["damage"], x["outcome"]):
                if o == "miss":
                    continue
                (crit if o == "crit" else hit)[min(bins - 1, int(d / width))] += 1 / n
            summary = summarize(x, s["hands"])
            m = s["hands"][0]["model"]
            summary["hit_chance"] = m["hitChance"]
            summary["crit_chance"] = m["critChance"]
        out.append({"value": s["value"], "on_grid": grid is None or s["value"] in grid,
                    "pob": s["pob"], "summary": summary,
                    "hit": [round(v, 6) for v in hit], "crit": [round(v, 6) for v in crit]})
    return {"bin_width": width, "bins": bins, "steps": out}
