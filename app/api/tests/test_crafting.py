"""Route planning over a hand-made engine result (no engine needed)."""

import crafting


def result(rarity, open_, candidate, current=(), replacing=None):
    pool = [
        {"side": "prefix", "group": "IncreasedLife", "levels": [60, 46, 33, 1]},
        {"side": "prefix", "group": "IncreasedMana", "levels": [60, 1]},
        {"side": "suffix", "group": "ColdResistance", "levels": [60, 1]},
    ]
    return {"rarity": rarity, "open": open_, "pool": pool, "current": list(current),
            "replacing": replacing, "candidates": [candidate], "corrupted": False}


LIFE = {"side": "prefix", "group": "IncreasedLife", "level": 60, "essences": [
    {"name": "Essence of the Body", "text": "+(85-99) to maximum Life"}]}


def test_rare_with_both_sides_open_needs_a_side_omen():
    r = crafting.plan(result("rare", {"prefix": 1, "suffix": 1}, dict(LIFE)))
    routes = r["candidates"][0]["routes"]
    exalt = next(rt for rt in routes if rt["steps"] == ["Exalted Orb"])
    assert exalt["omens"] == ["Omen of Sinistral Exaltation"]
    assert (exalt["hits"], exalt["outOf"]) == (4, 6)  # prefix tiers only
    perfect = next(rt for rt in routes if rt["steps"] == ["Perfect Exalted Orb"])
    assert (perfect["hits"], perfect["outOf"]) == (1, 2)
    essence = next(rt for rt in routes if rt["guaranteed"])
    assert essence["needsNewBase"]  # normal essences need a Magic item


def test_magic_item_uses_regal_with_coronation_and_essence_directly():
    r = crafting.plan(result("magic", {"prefix": 1, "suffix": 0}, dict(LIFE)))
    routes = r["candidates"][0]["routes"]
    assert any(rt["steps"] == ["Regal Orb"] and rt["omens"] == ["Omen of Sinistral Coronation"]
               for rt in routes)
    assert any(rt["steps"] == ["Essence of the Body"] and not rt.get("needsNewBase") for rt in routes)


def test_greater_tiers_drop_out_when_the_mod_is_below_their_floor():
    low = {"side": "prefix", "group": "IncreasedMana", "level": 30, "essences": []}
    res = result("rare", {"prefix": 1, "suffix": 0}, low)
    res["pool"][1]["levels"] = [30, 1]
    routes = crafting.plan(res)["candidates"][0]["routes"]
    assert [rt["steps"] for rt in routes] == [["Exalted Orb"]]


def test_corrupted_items_get_no_routes():
    res = result("rare", {"prefix": 1, "suffix": 0}, dict(LIFE))
    res["corrupted"] = True
    out = crafting.plan(res)
    assert out["blocked"] and out["candidates"][0]["routes"] == []
