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
    shred = next(g for g in s["skills"] if g["isMain"])
    # Regression: PoE2 keeps gem data on gem.gemData, which once hid every support gem.
    assert [x["name"] for x in shred["gems"] if x["support"]] == ["Elemental Armament II", "Rapid Attacks I", "Heft"]


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


def card_cell(card: dict, key: str) -> dict:
    return next(c for g in card["core"] for row in g["rows"] for c in row["cells"] if c["key"] == key)


def test_baseline_card_has_core_set(client, build):
    card = client.get(f"/builds/{build['build_id']}/card").json()
    assert [g["group"] for g in card["core"]] == ["Damage", "Pools", "Mitigation", "Survival"]
    life = card_cell(card, "Life")
    assert life["before"] == life["after"] == 1254 and "delta" not in life
    # Resistances and max hits each share one row.
    rows = {r.get("label"): r for g in card["core"] for r in g["rows"]}
    assert [c["key"] for c in rows["Resistances"]["cells"]] == \
        ["FireResist", "ColdResist", "LightningResist", "ChaosResist"]
    assert len(rows["Max hit"]["cells"]) == 5
    assert card["other"] == []


def test_item_compare_card_shows_every_core_stat(client, build):
    """Unchanged core stats stay on the card; changed ones carry delta and direction."""
    cmp = client.post(f"/builds/{build['build_id']}/items/compare", json={"slot": "Helmet", "item": HELMET}).json()
    card = cmp["card"]
    for key in ("FireResist", "ColdResist", "LightningResist", "ChaosResist", "Spirit"):
        card_cell(card, key)
    by_stat = {c["stat"]: c for c in cmp["changes"]["player"]}
    for key in ("Life", "ColdResist"):
        cell = card_cell(card, key)
        assert cell["after"] == pytest.approx(by_stat[key]["after"])
        assert cell["delta"] == pytest.approx(by_stat[key]["delta"])
        assert cell["better"] == by_stat[key]["better"]
    core_keys = {c["key"] for g in card["core"] for r in g["rows"] for c in r["cells"]}
    assert not core_keys & {c["stat"] for c in card["other"]}


def test_dashboard(client, build):
    bid = build["build_id"]
    data = client.get(f"/builds/{bid}/dashboard").json()
    assert data["build"]["ascendancy"] == "Shaman"
    assert data["baseline"] == client.get(f"/builds/{bid}/card").json()
    nodes = data["nodes"]["candidates"]
    assert len(nodes) == data["nodes"]["evaluated"] > 0
    assert all(n["cost"] <= 5 and n["path"][-1] == n["name"] and len(n["path"]) == n["cost"] for n in nodes)
    # Each node's card matches a direct what-if of that node.
    top = nodes[0]
    cmp = client.post(f"/builds/{bid}/nodes/compare", json={"add": [top["id"]]}).json()
    assert top["card"] == cmp["card"]
    # Cached until the build changes; download comes as a file.
    from main import sessions
    assert sessions.get(bid).dashboard is not None
    r = client.get(f"/builds/{bid}/dashboard", params={"download": True})
    assert "attachment" in r.headers["content-disposition"] and r.json()["generated_at"] == data["generated_at"]
    client.post(f"/builds/{bid}/items/equip", json={"slot": "Helmet", "item": HELMET})
    after = client.get(f"/builds/{bid}/dashboard").json()
    assert card_cell(after["baseline"], "Life")["before"] != card_cell(data["baseline"], "Life")["before"]


def test_item_details(client, build):
    r = client.get(f"/builds/{build['build_id']}/items", params={"slot": "Helmet"}).json()
    text = r["items"][0]["text"]
    assert "Victory Corona" in text and "+35% to Lightning Resistance" in text
    assert "{" not in text  # PoB's internal mod tags are cleaned up


def test_chat_endpoint(client, build, monkeypatch):
    import main
    from agent import Agent
    from fakes import FakeClient, FakeMessage, tool_call

    fake = FakeClient([
        FakeMessage(content=None, tool_calls=[tool_call(1, "get_stats", {"keys": ["ColdResist"]})]),
        FakeMessage(content="Your cold resistance is 12%.", tool_calls=None),
        FakeMessage(content="Still 12%.", tool_calls=None),
    ])

    def make_agent(session):
        async def engine_call(cmd, **args):
            return await main.sessions.call(session, cmd, **args)
        return Agent(engine_call=engine_call, model="fake", client=fake)

    monkeypatch.setattr(main, "make_agent", make_agent)
    bid = build["build_id"]
    r = client.post(f"/builds/{bid}/chat", json={"message": "cold res?"}).json()
    assert r["answer"] == "Your cold resistance is 12%."
    assert r["tool_calls"] == [{"name": "get_stats", "args": {"keys": ["ColdResist"]}, "ok": True, "summary": r["tool_calls"][0]["summary"]}]
    # The engine's real answer reached the model.
    assert any(m.get("content") == '{"ColdResist":12}' for m in fake.requests[1] if m["role"] == "tool")

    # The conversation continues on the same agent...
    r2 = client.post(f"/builds/{bid}/chat", json={"message": "and now?"}).json()
    assert r2["answer"] == "Still 12%."
    assert sum(m["role"] == "user" for m in fake.requests[-1]) == 2
    # ...until it's reset.
    assert client.delete(f"/builds/{bid}/chat").status_code == 204
    assert main.sessions.get(bid).agent.messages[-1]["role"] == "system"


def test_chat_without_llm_config_is_503(client, build, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    r = client.post(f"/builds/{build['build_id']}/chat", json={"message": "hi"})
    assert r.status_code == 503 and "not configured" in r.json()["detail"]


def test_chat_stream_events(client, build, monkeypatch):
    import json as _json
    import main
    from agent import Agent
    from fakes import FakeClient, FakeMessage, tool_call

    fake = FakeClient([
        FakeMessage(content=None, tool_calls=[tool_call(1, "get_stats", {"keys": ["Life"]})]),
        FakeMessage(content="1,254 life.", tool_calls=None),
    ])

    def make_agent(session):
        async def engine_call(cmd, **args):
            return await main.sessions.call(session, cmd, **args)
        return Agent(engine_call=engine_call, model="fake", client=fake)

    monkeypatch.setattr(main, "make_agent", make_agent)
    with client.stream("POST", f"/builds/{build['build_id']}/chat/stream", json={"message": "life?"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.read().decode()
    events = [(b.split("\n")[0].removeprefix("event: "), _json.loads(b.split("\n")[1].removeprefix("data: ")))
              for b in body.strip().split("\n\n")]
    assert [k for k, _ in events] == ["thinking", "tool", "thinking", "answer"]
    assert events[1][1]["name"] == "get_stats" and events[1][1]["ok"]
    assert events[-1][1]["answer"] == "1,254 life."


def test_chat_stream_reports_errors(client, build, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    with client.stream("POST", f"/builds/{build['build_id']}/chat/stream", json={"message": "hi"}) as r:
        body = r.read().decode()
    assert body.startswith("event: error") and "not configured" in body


def test_build_survives_server_restart(client, build):
    """Builds are saved to disk; after a restart (sessions forgotten) the id still works,
    including changes made before the restart."""
    import asyncio
    import main

    bid = build["build_id"]
    client.post(f"/builds/{bid}/items/equip", json={"slot": "Helmet", "item": HELMET})
    # Simulate a restart: stop the worker and forget every session.
    session = main.sessions.sessions.pop(bid)
    client.portal.call(session.worker.close) if hasattr(client, "portal") else asyncio.run(session.worker.close())
    r = client.get(f"/builds/{bid}")
    assert r.status_code == 200
    assert next(i["name"] for i in r.json()["items"] if i["slot"] == "Helmet") == "Doom Crown, Hallowed Crown"


def test_deleted_build_is_gone_for_good(client):
    bid = client.post("/builds", json={"xml": BUILD_XML}).json()["build_id"]
    assert client.delete(f"/builds/{bid}").status_code == 204
    import main
    main.sessions.sessions.pop(bid, None)
    assert client.get(f"/builds/{bid}").status_code == 404


def test_unknown_or_malformed_ids_are_404(client):
    assert client.get("/builds/000000000000").status_code == 404
    assert client.get("/builds/..%2F..%2Fetc").status_code == 404


def test_hit_distribution_matches_pob_averages(client, build):
    """The sampled model's exact mean is PoB's own average, in every scenario."""
    import distribution

    bid = build["build_id"]
    before = client.get(f"/builds/{bid}/stats", params={"keys": ["TotalDPS"]}).json()
    r = client.post(f"/builds/{bid}/distribution", json={
        "skill": "Lunar Assault", "samples": 5000,
        "scenarios": [{"name": "Base"}, {"name": "No companion", "config": {"companionInPresence": False}}],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["skill"] == "Lunar Assault"
    base, alone = data["scenarios"]
    for sc in (base, alone):
        s = sc["summary"]
        assert s["mean_per_attempt"] == pytest.approx(sc["pob"]["averageDamage"], rel=1e-9)
        assert s["sample_mean"] == pytest.approx(s["mean_per_attempt"], rel=0.05)
        assert len(sc["samples"]["damage"]) == 5000
        assert set(sc["samples"]["outcome"]) <= {"miss", "hit", "crit"}
        assert all(d == 0 for d, o in zip(sc["samples"]["damage"], sc["samples"]["outcome"]) if o == "miss")
        for h in sc["hands"]:
            assert distribution.model_mean(h["model"])["per_hit"] == pytest.approx(h["model"]["pob"]["averageHit"])
    assert alone["pob"]["averageDamage"] < base["pob"]["averageDamage"]
    # Scenarios never change the build itself.
    assert client.get(f"/builds/{bid}/stats", params={"keys": ["TotalDPS"]}).json() == before
    cfg = client.get(f"/builds/{bid}/config", params={"query": "companionInPresence"}).json()
    assert cfg["options"][0]["value"] is True


def test_hit_distribution_rejects_unknown_config(client, build):
    r = client.post(f"/builds/{build['build_id']}/distribution",
                    json={"skill": "Shred", "scenarios": [{"name": "x", "config": {"notAVar": True}}]})
    assert r.status_code == 422 and "notAVar" in r.json()["detail"]


def test_hit_distribution_of_a_skill_without_hits_is_422(client, build):
    r = client.post(f"/builds/{build['build_id']}/distribution", json={"skill": "Purity of Fire", "samples": 100})
    assert r.status_code == 422 and "doesn't deal hit damage" in r.json()["detail"]
