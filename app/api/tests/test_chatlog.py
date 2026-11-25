"""Chat history: the store on its own, then through the API with the real engine."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import buildcode
from chatlog import ChatLog
from fakes import FakeClient, FakeMessage, tool_call

BUILD_XML = (Path(__file__).resolve().parent / "fixtures" / "shaman.xml").read_text()


# -- store --

def test_conversation_roundtrip(tmp_path):
    log = ChatLog(tmp_path)
    cid = log.start(character="Mira", snapshot_id="a" * 16, build_id="b" * 12, model="m")
    log.turn(cid, question="life?", answer="1,254", tool_calls=[{"name": "get_stats"}],
             messages=[{"role": "user", "content": "life?"}], tokens=10, duration_ms=5)
    log.turn(cid, question="why?", error="model down")

    conv = log.get(cid)
    assert conv["character"] == "Mira" and conv["snapshot_id"] == "a" * 16
    assert [t["question"] for t in conv["turns"]] == ["life?", "why?"]
    assert conv["turns"][1]["error"] == "model down"
    assert "messages" not in conv["turns"][0]          # display copy leaves the raw trace out
    assert log.read(cid)[1]["messages"][0]["content"] == "life?"  # ...but the file keeps it


def test_list_filters_by_character_and_skips_empty(tmp_path):
    log = ChatLog(tmp_path)
    a = log.start(character="Mira", snapshot_id=None, build_id="x", model="m")
    log.turn(a, question="first question", answer="ok")
    log.start(character="Mira", snapshot_id=None, build_id="x", model="m")  # never asked anything
    b = log.start(character="Oskar", snapshot_id=None, build_id="y", model="m")
    log.turn(b, question="other", answer="ok")

    mira = log.list("Mira")
    assert [c["id"] for c in mira] == [a]
    assert mira[0]["title"] == "first question" and mira[0]["turns"] == 1
    assert {c["id"] for c in log.list()} == {a, b}


def test_torn_last_line_is_skipped(tmp_path):
    log = ChatLog(tmp_path)
    cid = log.start(character="Mira", snapshot_id=None, build_id="x", model="m")
    log.turn(cid, question="q", answer="a")
    with (tmp_path / f"{cid}.jsonl").open("a") as f:
        f.write('{"type": "turn", "quest')
    assert len(log.get(cid)["turns"]) == 1


def test_bad_ids_are_not_found(tmp_path):
    log = ChatLog(tmp_path)
    assert log.get("../etc/passwd") is None and log.get("0" * 16) is None


def test_rename_character_moves_a_snapshots_chats(tmp_path):
    log = ChatLog(tmp_path)
    cid = log.start(character="Druid", snapshot_id="s" * 16, build_id="x", model="m")
    log.turn(cid, question="q", answer="a")
    other = log.start(character="Druid", snapshot_id="t" * 16, build_id="x", model="m")
    log.turn(other, question="q", answer="a")
    log.rename_character("s" * 16, "Mira")
    assert log.get(cid)["character"] == "Mira"
    assert log.get(other)["character"] == "Druid"
    assert len(log.read(cid)) == 2


# -- API --

@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


def fake_agent(monkeypatch, turns):
    import main
    from agent import Agent
    fake = FakeClient(turns)

    def make_agent(session):
        async def engine_call(cmd, **args):
            return await main.sessions.call(session, cmd, **args)
        return Agent(engine_call=engine_call, model="fake", client=fake)

    monkeypatch.setattr(main, "make_agent", make_agent)


def test_chats_are_logged_per_character_and_snapshot(client, monkeypatch):
    fake_agent(monkeypatch, [
        FakeMessage(content=None, tool_calls=[tool_call(1, "get_stats", {"keys": ["Life"]})]),
        FakeMessage(content="1,254 life.", tool_calls=None),
        FakeMessage(content="Still 1,254.", tool_calls=None),
        FakeMessage(content="New chat.", tool_calls=None),
    ])
    r = client.post("/builds", json={"code": buildcode.encode(BUILD_XML), "character": "Chatlog Mira"})
    assert r.status_code == 201
    body = r.json()
    bid, snap_id = body["build_id"], body["snapshot"]["id"]
    assert body["character"] == "Chatlog Mira"
    try:
        client.post(f"/builds/{bid}/chat", json={"message": "life?"})
        client.post(f"/builds/{bid}/chat", json={"message": "sure?"})
        client.delete(f"/builds/{bid}/chat")
        client.post(f"/builds/{bid}/chat", json={"message": "hello again"})

        listed = client.get("/chats", params={"character": "Chatlog Mira"}).json()
        assert [c["title"] for c in listed] == ["hello again", "life?"]
        assert all(c["snapshot_id"] == snap_id for c in listed)

        first = client.get(f"/chats/{listed[1]['id']}").json()
        assert [t["answer"] for t in first["turns"]] == ["1,254 life.", "Still 1,254."]
        assert first["turns"][0]["tool_calls"][0]["name"] == "get_stats"

        # The raw file keeps what the model saw: the build context, and the engine's result.
        import main
        raw = main.chats.read(listed[1]["id"])
        msgs = raw[1]["messages"]
        assert msgs[0]["role"] == "system" and "Build snapshot" in msgs[0]["content"]
        assert any(m["role"] == "tool" and "Life" in m["content"] for m in msgs)
        assert raw[2]["messages"][0] == {"role": "user", "content": "sure?"}

        # Moving the snapshot to another character takes its chats along.
        client.patch(f"/snapshots/{snap_id}", json={"character": "Chatlog Renamed"})
        assert client.get("/chats", params={"character": "Chatlog Mira"}).json() == []
        assert len(client.get("/chats", params={"character": "Chatlog Renamed"}).json()) == 2
    finally:
        client.delete(f"/builds/{bid}")
        client.delete(f"/snapshots/{snap_id}")


def test_failed_question_is_logged(client, monkeypatch):
    import settings
    fake_agent(monkeypatch, [])  # no scripted turns: the model call raises
    r = client.post("/builds", json={"code": buildcode.encode(BUILD_XML), "character": "Chatlog Err"})
    bid, snap_id = r.json()["build_id"], r.json()["snapshot"]["id"]
    try:
        assert client.post(f"/builds/{bid}/chat", json={"message": "boom?"}).status_code == 502
        [conv] = client.get("/chats", params={"character": "Chatlog Err"}).json()
        turn = client.get(f"/chats/{conv['id']}").json()["turns"][0]
        assert turn["question"] == "boom?" and "language model request failed" in turn["error"]
    finally:
        client.delete(f"/builds/{bid}")
        client.delete(f"/snapshots/{snap_id}")


def test_opened_snapshot_keeps_its_character(client):
    r = client.post("/builds", json={"code": buildcode.encode(BUILD_XML), "character": "Chatlog Open"})
    snap_id = r.json()["snapshot"]["id"]
    client.delete(f"/builds/{r.json()['build_id']}")
    try:
        opened = client.post(f"/snapshots/{snap_id}/load").json()
        assert opened["character"] == "Chatlog Open" and opened["snapshot"]["id"] == snap_id
        import main
        session = main.sessions.get(opened["build_id"])
        assert (session.character, session.snapshot_id) == ("Chatlog Open", snap_id)
        client.delete(f"/builds/{opened['build_id']}")
    finally:
        client.delete(f"/snapshots/{snap_id}")


def test_unknown_chat_is_404(client):
    assert client.get("/chats/" + "0" * 16).status_code == 404
    assert client.get("/chats/nope").status_code == 404
