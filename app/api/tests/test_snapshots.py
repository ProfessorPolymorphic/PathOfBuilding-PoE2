"""Snapshot history: the store on its own, then through the API with the real engine."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import buildcode
import snapshots
from snapshots import SnapshotStore, content_hash

BUILD_XML = (Path(__file__).resolve().parent / "fixtures" / "shaman.xml").read_text()
SUMMARY = {"class": "Druid", "ascendancy": "Shaman", "level": 64, "mainSkill": "Shred",
           "stats": {"player": [{"stat": "Life", "label": "Life", "value": 1254}]}}
T0 = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)


def relevel(xml: str, level: int) -> str:
    return xml.replace('level="64"', f'level="{level}"', 1)


# -- store --

def test_hash_ignores_cached_stats_indentation_and_blank_lines():
    noisy = BUILD_XML.replace('stat="Life" value="', 'stat="Life" value="9').replace("\t", "  ").replace("\n", "\r\n\n")
    assert content_hash(noisy) == content_hash(BUILD_XML)
    assert content_hash(relevel(BUILD_XML, 65)) != content_hash(BUILD_XML)


def test_hash_keeps_line_breaks_inside_item_text():
    # Item text is parsed line by line: joining two mod lines changes the item.
    joined = BUILD_XML.replace("Hallowed Crown\n", "Hallowed Crown ", 1)
    assert joined != BUILD_XML
    assert content_hash(joined) != content_hash(BUILD_XML)


def test_same_build_is_stored_once(tmp_path):
    store = SnapshotStore(tmp_path)
    first, created = store.add(BUILD_XML, SUMMARY, now=T0)
    again, created_again = store.add(BUILD_XML, SUMMARY, now=T0 + timedelta(hours=1))
    assert created and not created_again
    assert again == first  # the original paste time is kept
    assert len(store.list()) == 1
    assert store.xml(first["id"]) == BUILD_XML


def test_same_build_under_two_characters_is_kept_for_both(tmp_path):
    store = SnapshotStore(tmp_path)
    a, _ = store.add(BUILD_XML, SUMMARY, character="A")
    b, created = store.add(BUILD_XML, SUMMARY, character="B")
    assert created and a["id"] != b["id"]
    assert [len(store.list(c)) for c in ("A", "B")] == [1, 1]
    assert store.add(BUILD_XML, SUMMARY, character="B")[1] is False


def test_character_defaults_to_class(tmp_path):
    store = SnapshotStore(tmp_path)
    assert store.add(BUILD_XML, SUMMARY)[0]["character"] == "Druid"
    assert store.add(relevel(BUILD_XML, 70), SUMMARY, character="  Leafy ")[0]["character"] == "Leafy"


def test_list_and_characters(tmp_path):
    store = SnapshotStore(tmp_path)
    for i, (lvl, char) in enumerate([(64, "A"), (66, "B"), (65, "A")]):
        store.add(relevel(BUILD_XML, lvl), {**SUMMARY, "level": lvl}, character=char, now=T0 + timedelta(days=i))
    assert [m["level"] for m in store.list("A")] == [64, 65]  # oldest first
    chars = store.characters()
    assert [(c["character"], c["snapshots"], c["level"]) for c in chars] == [("A", 2, 65), ("B", 1, 66)]
    assert chars[0]["first_taken_at"] < chars[0]["last_taken_at"]


def test_rename_delete_and_bad_ids(tmp_path):
    store = SnapshotStore(tmp_path)
    meta, _ = store.add(BUILD_XML, SUMMARY)
    assert store.rename(meta["id"], "Leafy")["character"] == "Leafy"
    assert store.get(meta["id"])["character"] == "Leafy"
    assert store.delete(meta["id"]) and store.get(meta["id"]) is None and store.list() == []
    assert store.get("../../etc/passwd") is None and store.xml("nope") is None


def test_listing_flattens_stats():
    row = snapshots.listing({"id": "x", "summary": SUMMARY, "level": 64})
    assert row == {"id": "x", "level": 64, "stats": {"Life": 1254}}


# -- API --

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "history", SnapshotStore(tmp_path / "snapshots"))
    with TestClient(main.app) as c:
        yield c


def test_paste_records_snapshot_once(client):
    code = buildcode.encode(BUILD_XML)
    r1 = client.post("/builds", json={"code": code}).json()
    r2 = client.post("/builds", json={"code": code}).json()
    assert r1["snapshot"]["created"] and not r2["snapshot"]["created"]
    assert r1["snapshot"]["id"] == r2["snapshot"]["id"]
    assert r1["snapshot"]["character"] == "Druid"

    rows = client.get("/snapshots", params={"character": "Druid"}).json()
    assert len(rows) == 1
    assert rows[0]["level"] == 64 and rows[0]["main_skill"] == "Shred"
    assert rows[0]["stats"]["Life"] == 1254 and "summary" not in rows[0]
    assert client.get("/characters").json()[0]["snapshots"] == 1
    for r in (r1, r2):
        client.delete(f"/builds/{r['build_id']}")


def test_what_ifs_do_not_change_the_snapshot(client):
    r = client.post("/builds", json={"xml": BUILD_XML, "character": "Leafy"}).json()
    snap_id, bid = r["snapshot"]["id"], r["build_id"]
    client.post(f"/builds/{bid}/nodes/apply", json={"add": ["Dimensional Weakspot"]})
    assert client.get(f"/snapshots/{snap_id}", params={"include_xml": True}).json()["xml"] == BUILD_XML

    # A snapshot can be reopened as a working build, without recording a new one.
    loaded = client.post(f"/snapshots/{snap_id}/load")
    assert loaded.status_code == 201 and loaded.json()["summary"]["level"] == 64
    assert len(client.get("/snapshots").json()) == 1
    for b in (bid, loaded.json()["build_id"]):
        client.delete(f"/builds/{b}")


def test_snapshot_rename_and_delete(client):
    r = client.post("/builds", json={"xml": BUILD_XML}).json()
    snap_id = r["snapshot"]["id"]
    assert client.patch(f"/snapshots/{snap_id}", json={"character": "Leafy"}).json()["character"] == "Leafy"
    assert [c["character"] for c in client.get("/characters").json()] == ["Leafy"]
    assert client.delete(f"/snapshots/{snap_id}").status_code == 204
    assert client.get(f"/snapshots/{snap_id}").status_code == 404
    assert client.get("/snapshots/not-an-id").status_code == 404
    client.delete(f"/builds/{r['build_id']}")


def test_snapshot_failure_does_not_block_loading(client, monkeypatch):
    import main

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(main.history, "add", broken)
    r = client.post("/builds", json={"xml": BUILD_XML})
    assert r.status_code == 201 and r.json()["snapshot"] is None
    client.delete(f"/builds/{r.json()['build_id']}")
