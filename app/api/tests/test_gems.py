"""Engine tests for skill-group what-ifs: support contributions, gem swaps, skill costs.

Uses the fixture build (Druid/Shaman): Lunar Assault (group 9) has Rage III, Ice Bite II,
Elemental Armament II and Rapid Attacks II; Arctic Howl (group 6) also has Ice Bite II.
"""

from pathlib import Path

import pytest

from engine import EngineError, EngineWorker

BUILD_XML = (Path(__file__).resolve().parent / "fixtures" / "shaman.xml").read_text()


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def worker():
    w = EngineWorker()
    await w.start()
    await w.call("load_xml", xml=BUILD_XML)
    yield w
    await w.close()


async def fingerprint(w):
    """Enough of the build's state to tell whether a what-if left it changed."""
    s = await w.call("summary")
    stats = await w.call("stats", keys=["CombinedDPS", "Life", "Mana", "ManaCost"])
    config = await w.call("list_config", query="", set_only=True)
    return s["mainSkill"], [[g["name"] for g in grp["gems"]] for grp in s["skills"]], stats, config


@pytest.mark.anyio
async def test_support_contributions_reports_cost_and_why(worker):
    before = await fingerprint(worker)
    await worker.call("set_config", var="conditionFrozenEnemyRecently", value=True)
    try:
        r = await worker.call("support_contributions", group="Lunar Assault")
    finally:
        await worker.call("set_config", var="conditionFrozenEnemyRecently", value=None)
    rows = {row["support"]: row for row in r["supports"]}
    assert r["withAllSupports"]["ManaCost"] > 0
    # Elemental Armament adds cost per use.
    assert rows["Elemental Armament II"]["cost"]["ManaCost"]["added"] > 0
    # Ice Bite's buff also comes from Arctic Howl's copy: removing this one changes nothing,
    # removing every copy costs damage.
    ice = rows["Ice Bite II"]
    assert ice["primaryLost"] == 0
    assert ice["alsoIn"][0]["skill"] == "Arctic Howl"
    assert ice["lostIfAllCopiesRemoved"] > 0
    assert ice["needs"][0]["configVar"] == "conditionFrozenEnemyRecently"
    assert await fingerprint(worker) == before


@pytest.mark.anyio
async def test_support_needs_names_the_missing_condition(worker):
    before = await fingerprint(worker)
    r = await worker.call("support_contributions", group="Lunar Assault")
    ice = next(row for row in r["supports"] if row["support"] == "Ice Bite II")
    assert ice["needs"][0]["enabled"] is False
    assert "Have you Frozen an enemy Recently?" in ice["note"] and "Arctic Howl" in ice["note"]
    # With the condition off the buff is worth nothing; switched on, every copy together is.
    assert ice["lostIfAllCopiesRemoved"] == 0
    assert ice["ifConditionsOn"]["config"] == ["conditionFrozenEnemyRecently"]
    assert ice["ifConditionsOn"]["lost"] == 0
    assert ice["ifConditionsOn"]["lostIfAllCopiesRemoved"] > 0
    assert await fingerprint(worker) == before  # config restored too


@pytest.mark.anyio
async def test_compare_gems_swap(worker):
    before = await fingerprint(worker)
    r = await worker.call("compare_gems", group="Lunar Assault", remove=["Ice Bite II"], add=["Efficiency II"])
    assert r["skill"] == "Lunar Assault"
    assert (r["removed"], r["added"]) == (["Ice Bite II"], ["Efficiency II"])
    assert r["cost"]["ManaCost"]["delta"] < 0
    assert r["mana"]["after"]["netPerSecond"] > r["mana"]["before"]["netPerSecond"]
    focus = {c["key"]: c for c in r["card"]["focus"]}
    assert focus["ManaCost"]["better"] is True and focus["ManaNetPerSecond"]["better"] is True
    assert "warnings" not in r
    # Why removing Ice Bite costs nothing here, and what the swap does when you freeze.
    ice = next(a for a in r["about"] if a["support"] == "Ice Bite II")
    assert ice["role"] == "removed" and ice["buffAlsoFrom"] == ["Arctic Howl"]
    assert ice["needsOff"] == ["Have you Frozen an enemy Recently?"]
    assert r["ifConditionsOn"]["config"] == ["conditionFrozenEnemyRecently"]
    assert r["ifConditionsOn"]["cost"]["ManaCost"]["delta"] < 0
    assert await fingerprint(worker) == before


@pytest.mark.anyio
async def test_compare_gems_warnings_and_errors(worker):
    r = await worker.call("compare_gems", group="Lunar Assault", add=["Efficiency II", "Ice Bite I"])
    assert any("at most 5" in w for w in r["warnings"])
    # Two Ice Bites: PoB keeps one and drops the other; either way the user is told.
    assert any(w.startswith("Ice Bite") and "doesn't apply" in w for w in r["warnings"])
    with pytest.raises(EngineError, match="no support 'Nope'"):
        await worker.call("compare_gems", group="Lunar Assault", remove=["Nope"])
    with pytest.raises(EngineError, match="did you mean"):
        await worker.call("compare_gems", group="Lunar Assault", add=["Efficiency"])
    with pytest.raises(EngineError, match="not a support"):
        await worker.call("compare_gems", group="Lunar Assault", add=["Shred"])


@pytest.mark.anyio
async def test_skill_costs(worker):
    before = await fingerprint(worker)
    r = await worker.call("skill_costs")
    by = {s["skill"]: s for s in r["skills"]}
    lunar = by["Lunar Assault"]
    m = lunar["mana"]
    assert lunar["costs"]["ManaCost"] == m["costPerUse"] > 0
    assert m["netPerSecond"] == pytest.approx(m["regenPerSecond"] + m["leechAndOnHitPerSecond"] - m["spendPerSecond"], abs=0.01)
    assert m["secondsToEmpty"] == pytest.approx(m["pool"] / -m["netPerSecond"], rel=1e-3)
    assert by["Shred"]["isMain"]
    assert await fingerprint(worker) == before


@pytest.mark.anyio
async def test_compare_item_lists_changed_mods(worker):
    r = await worker.call("compare_item", slot="Helmet", item="""Item Class: Helmets
Rarity: Rare
Doom Crown
Hallowed Crown
--------
+80 to maximum Life""", focus=["Life"])
    assert r["modsAdded"] == ["+80 to maximum Life"]
    assert r["modsRemoved"]
    assert r["card"]["focus"][0]["key"] == "Life"
