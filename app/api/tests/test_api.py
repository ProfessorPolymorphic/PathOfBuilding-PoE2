"""Integration tests: real HTTP -> FastAPI -> LuaJIT worker -> PoB engine.

Requires the engine toolchain (app/engine/setup.sh). Run:
    app/api/.venv/bin/pytest app/api/tests -q
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import buildcode
from main import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Level 64 Druid/Shaman (PoE2, tree 0_5), Shred main skill.
BUILD_XML = (FIXTURES / "shaman.xml").read_text()

HELMET = """Item Class: Helmets
Rarity: Rare
Doom Crown
Hallowed Crown
--------
Item Level: 70
--------
+80 to maximum Life
+40% to Cold Resistance
+30% to Lightning Resistance"""


def stat(stats: dict, key: str):
    return next(s["value"] for s in stats["player"] if s["stat"] == key)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def build(client):
    """A fresh session with the test build loaded via a share code."""
    r = client.post("/builds", json={"code": buildcode.encode(BUILD_XML), "name": "shaman"})
    assert r.status_code == 201, r.text
    body = r.json()
    yield body
    client.delete(f"/builds/{body['build_id']}")


def test_buildcode_roundtrip():
    assert buildcode.decode(buildcode.encode(BUILD_XML)) == BUILD_XML


def test_load_summary(build):
    s = build["summary"]
    assert (s["class"], s["ascendancy"], s["level"]) == ("Druid", "Shaman", 64)
    assert s["mainSkill"] == "Shred"
    assert stat(s["stats"], "Life") == 1254
    assert all(g["activeSkill"] for g in s["skills"])


def test_invalid_code(client):
    r = client.post("/builds", json={"code": "definitely-not-a-build"})
    assert r.status_code == 400


def test_compare_nodes_matches_apply(client, build):
    bid = build["build_id"]
    before = stat(build["summary"]["stats"], "TotalDPS")
    cmp = client.post(f"/builds/{bid}/nodes/compare", json={"add": ["Dimensional Weakspot"]}).json()
    dps_change = next(c for c in cmp["changes"]["player"] if c["stat"] == "TotalDPS")
    assert dps_change["before"] == pytest.approx(before)
    assert cmp["pointDelta"] == len(cmp["added"]) > 0

    # Compare was a what-if: the build must be unchanged...
    assert stat(client.get(f"/builds/{bid}/stats").json(), "TotalDPS") == pytest.approx(before)
    # ...and applying it must produce exactly the predicted value.
    applied = client.post(f"/builds/{bid}/nodes/apply", json={"add": ["Dimensional Weakspot"]}).json()
    assert stat(applied["stats"], "TotalDPS") == pytest.approx(dps_change["after"])


def test_compare_reports_skipped(client, build):
    r = client.post(f"/builds/{build['build_id']}/nodes/compare", json={"remove": ["Dimensional Weakspot"]}).json()
    assert r["skipped"] == [{"node": "Dimensional Weakspot", "reason": "not allocated"}]


def test_unknown_node_is_422(client, build):
    r = client.post(f"/builds/{build['build_id']}/nodes/compare", json={"add": ["Not A Real Node"]})
    assert r.status_code == 422 and "no passive node" in r.json()["detail"]


def test_item_compare_and_equip(client, build):
    bid = build["build_id"]
    cmp = client.post(f"/builds/{bid}/items/compare", json={"slot": "Helmet", "item": HELMET}).json()
    assert cmp["current"] == "Victory Corona, Hallowed Crown"
    life = next(c for c in cmp["changes"]["player"] if c["stat"] == "Life")
    equipped = client.post(f"/builds/{bid}/items/equip", json={"slot": "Helmet", "item": HELMET}).json()
    assert stat(equipped["stats"], "Life") == pytest.approx(life["after"])


def test_bad_slot_is_422(client, build):
    r = client.post(f"/builds/{build['build_id']}/items/compare", json={"slot": "Hat", "item": HELMET})
    assert r.status_code == 422 and "Helmet" in r.json()["detail"]


def test_config_roundtrip(client, build):
    bid = build["build_id"]
    base = stat(build["summary"]["stats"], "TotalDPS")
    client.put(f"/builds/{bid}/config/conditionEnemyShocked", json={"value": True})
    shocked = client.put(f"/builds/{bid}/config/conditionShockEffect", json={"value": 30}).json()
    assert stat(shocked["stats"], "TotalDPS") > base
    cleared = client.put(f"/builds/{bid}/config/conditionEnemyShocked", json={"value": None}).json()
    assert stat(cleared["stats"], "TotalDPS") == pytest.approx(base)


def test_export_roundtrip_keeps_changes(client, build):
    bid = build["build_id"]
    client.post(f"/builds/{bid}/items/equip", json={"slot": "Helmet", "item": HELMET})
    code = client.get(f"/builds/{bid}/export").json()["code"]
    reloaded = client.post("/builds", json={"code": code}).json()
    helmet = next(i["name"] for i in reloaded["summary"]["items"] if i["slot"] == "Helmet")
    assert helmet == "Doom Crown, Hallowed Crown"
    client.delete(f"/builds/{reloaded['build_id']}")


def test_worker_crash_recovery(client, build):
    """Killing the worker mid-session must not lose the (modified) build."""
    from main import sessions

    bid = build["build_id"]
    client.post(f"/builds/{bid}/items/equip", json={"slot": "Helmet", "item": HELMET})
    sessions.get(bid).worker._proc.kill()
    # The in-flight call may fail; the following one must restart and restore state.
    for _ in range(2):
        r = client.get(f"/builds/{bid}")
        if r.status_code == 200:
            break
    assert r.status_code == 200
    assert next(i["name"] for i in r.json()["items"] if i["slot"] == "Helmet") == "Doom Crown, Hallowed Crown"


def test_rank_nodes_matches_compare(client, build):
    bid = build["build_id"]
    r = client.post(f"/builds/{bid}/nodes/rank", json={"stat": "TotalDPS", "max_cost": 4, "limit": 3}).json()
    assert r["evaluated"] > 0 and len(r["results"]) == 3
    deltas = [x["delta"] for x in r["results"]]
    assert deltas == sorted(deltas, reverse=True)
    # The ranked gain for the top node must equal a direct what-if of that node.
    top = r["results"][0]
    cmp = client.post(f"/builds/{bid}/nodes/compare", json={"add": [top["name"]]}).json()
    dps = next(c for c in cmp["changes"]["player"] if c["stat"] == "TotalDPS")
    assert dps["delta"] == pytest.approx(top["delta"])
    assert cmp["pointDelta"] == top["cost"]


def test_item_details(client, build):
    r = client.get(f"/builds/{build['build_id']}/items", params={"slot": "Helmet"}).json()
    text = r["items"][0]["text"]
    assert "Victory Corona" in text and "+35% to Lightning Resistance" in text
    assert "{" not in text  # PoB's internal mod tags are cleaned up
