"""Agent tests with a fake model and a real engine -- no LLM calls, no network."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent as agent_mod
from agent import TOOLS, Agent, _engine_request
from engine import EngineWorker
from fakes import FakeClient, FakeMessage, tool_call

BUILD_XML = (Path(__file__).resolve().parent / "fixtures" / "shaman.xml").read_text()


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


def test_every_tool_maps_to_an_engine_command():
    for tool in TOOLS:
        cmd, _ = _engine_request(tool["function"]["name"], {})
        assert cmd


@pytest.mark.anyio
async def test_agent_runs_tools_against_engine():
    worker = EngineWorker()
    await worker.start()
    try:
        await worker.call("load_xml", xml=BUILD_XML)
        client = FakeClient([
            FakeMessage(content=None, tool_calls=[
                tool_call(1, "get_stats", {"keys": ["ColdResist"]}),
                tool_call(2, "compare_passives", {"add": ["Not A Node"]}),
            ]),
            FakeMessage(content="Cold res is 12%.", tool_calls=None),
        ])
        events = []
        a = Agent(engine_call=worker.call, model="fake", client=client, on_tool=events.append)
        assert await a.ask("cold res?") == "Cold res is 12%."

        tool_msgs = [m for m in client.requests[1] if m["role"] == "tool"]
        assert json.loads(tool_msgs[0]["content"]) == {"ColdResist": 12}
        assert "no passive node" in json.loads(tool_msgs[1]["content"])["error"]
        assert [e.ok for e in events] == [True, False]
    finally:
        await worker.close()


HELMET = """Item Class: Helmets
Rarity: Rare
Doom Crown
Hallowed Crown
--------
+80 to maximum Life
+40% to Cold Resistance"""


@pytest.mark.anyio
async def test_compare_card_goes_to_user_not_model():
    worker = EngineWorker()
    await worker.start()
    try:
        await worker.call("load_xml", xml=BUILD_XML)
        client = FakeClient([
            FakeMessage(content=None, tool_calls=[
                tool_call(1, "compare_item", {"slot": "Helmet", "item_text": HELMET}),
            ]),
            FakeMessage(content="Done.", tool_calls=None),
        ])
        events = []
        a = Agent(engine_call=worker.call, model="fake", client=client, on_tool=events.append)
        await a.ask("is this helmet better?")
        tool_msg = next(m for m in client.requests[1] if m["role"] == "tool")
        assert "card" not in json.loads(tool_msg["content"])
        assert events[0].card and events[0].card["core"][0]["group"] == "Damage"
        assert events[0].card_title == "Helmet: Doom Crown, Hallowed Crown (replacing Victory Corona, Hallowed Crown)"
        assert "+ +80 to maximum Life" in events[0].card_detail
        assert any(line.startswith("− ") for line in events[0].card_detail)
    finally:
        await worker.close()


@pytest.mark.anyio
async def test_failed_question_is_rolled_back():
    async def engine_call(cmd, **args):
        return {}

    class Boom(Exception):
        pass

    async def explode(**kwargs):
        raise Boom()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=explode)))
    a = Agent(engine_call=engine_call, model="fake", client=client)
    before = list(a.messages)
    with pytest.raises(Boom):
        await a.ask("hello")
    assert a.messages == before


def test_tool_round_limit(monkeypatch):
    monkeypatch.setattr(agent_mod, "MAX_TOOL_ROUNDS", 1)
    # On the final round the agent must forbid further tool calls.
    import asyncio

    async def engine_call(cmd, **args):
        return {"ok": True}

    client = FakeClient([
        FakeMessage(content=None, tool_calls=[tool_call(1, "get_build_summary", {})]),
        FakeMessage(content="done", tool_calls=None),
    ])
    seen_choice = []
    orig = client._create

    async def spy(**kwargs):
        seen_choice.append(kwargs["tool_choice"])
        return await orig(**kwargs)

    client.chat.completions.create = spy
    a = Agent(engine_call=engine_call, model="fake", client=client)
    assert asyncio.run(a.ask("x")) == "done"
    assert seen_choice == ["required", "none"]


def test_card_titles():
    from agent import _card_detail, _card_title
    gems = {"skill": "Lunar Assault", "removed": ["Ice Bite II"], "added": ["Efficiency II"],
            "warnings": ["too many supports"]}
    assert _card_title(gems) == "Lunar Assault: −Ice Bite II, +Efficiency II"
    assert _card_detail(gems) == ["⚠ too many supports"]
    assert _card_title({"added": ["A", "B"], "removed": []}) == "+A, B"
    # A what-if that tested nothing gets no card.
    assert _card_title({"added": [], "removed": [], "skipped": [{"node": "X", "reason": "already allocated"}]}) is None


@pytest.mark.anyio
async def test_skipped_passive_compare_shows_no_card():
    worker = EngineWorker()
    await worker.start()
    try:
        await worker.call("load_xml", xml=BUILD_XML)
        allocated = (await worker.call("search_nodes", allocated=True, limit=1))
        node = (allocated.get("nodes") or allocated.get("results"))[0]["name"]
        client = FakeClient([
            FakeMessage(content=None, tool_calls=[tool_call(1, "compare_passives", {"add": [node]})]),
            FakeMessage(content="Done.", tool_calls=None),
        ])
        events = []
        a = Agent(engine_call=worker.call, model="fake", client=client, on_tool=events.append)
        await a.ask("what if?")
        assert events[0].ok and events[0].card is None
        assert events[0].summary.startswith("nothing tested:") and "already allocated" in events[0].summary
    finally:
        await worker.close()


@pytest.mark.anyio
async def test_snapshot_lists_skill_costs():
    worker = EngineWorker()
    await worker.start()
    try:
        await worker.call("load_xml", xml=BUILD_XML)
        a = Agent(engine_call=worker.call, model="fake", client=FakeClient([]))
        context = await a._build_context()
        assert "The main skill (Shred) has no mana cost" in context
        assert "Lunar Assault (group 9)" in context
        snapshot = json.loads(context.split("setting):\n", 1)[1])
        lunar = next(s for s in snapshot["skills"] if s["active"] == "Lunar Assault")
        assert lunar["costs"]["ManaCost"] > 0 and lunar["manaNetPerSecond"] < 0
        shred = next(s for s in snapshot["skills"] if s["active"] == "Shred")
        assert shred["main"] and "costs" not in shred
    finally:
        await worker.close()
