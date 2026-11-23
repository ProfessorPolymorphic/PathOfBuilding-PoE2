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
