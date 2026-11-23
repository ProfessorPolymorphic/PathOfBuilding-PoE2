"""History of pasted builds, one snapshot per distinct paste.

Every build loaded from a code is kept with the time it was pasted, the character's level and
the engine's summary (sidebar stats, skills, items). The progress view draws from these.

Snapshots never change after they are written, unlike the working copies in STATE_DIR that
what-ifs and equips modify. Layout, one pair per snapshot:

    <dir>/<id>.json     metadata + engine summary
    <dir>/<id>.xml.gz   the build XML as pasted

Each snapshot records a hash of the build's content, so pasting the same build twice for the
same character finds the existing snapshot instead of storing a duplicate. The same build pasted
under another character gets its own snapshot, so neither character's history has a gap.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pob.snapshots")

SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# PlayerStat/MinionStat elements are a cache of stats PoB computed when it saved the build.
# They don't describe the build itself, and two exports of the same build can differ in them.
_STAT_CACHE_RE = re.compile(r"<(?:Player|Minion)Stat\b[^>]*/>")


def content_hash(xml: str) -> str:
    """Stable fingerprint of a build: ignores PoB's cached stats, indentation and blank lines.
    Line breaks are kept: item text is parsed line by line, so joining two mod lines changes
    the item. PoB trims each item line and skips blank ones (Item.lua, ParseRaw), so those
    differences don't change the build and are dropped here too."""
    lines = (line.strip() for line in _STAT_CACHE_RE.sub("", xml).splitlines())
    canonical = "\n".join(line for line in lines if line)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def default_character(summary: dict) -> str:
    """Snapshots are grouped by character. The build code has no character name, so without
    one from the user the class stands in (not the ascendancy, which changes partway through
    the campaign and would split one character's history in two)."""
    return summary.get("class") or "Unknown"


class SnapshotStore:
    def __init__(self, directory: Path):
        self.dir = directory

    def _paths(self, snap_id: str) -> tuple[Path, Path]:
        return self.dir / f"{snap_id}.json", self.dir / f"{snap_id}.xml.gz"

    def add(self, xml: str, summary: dict, character: str | None = None,
            now: datetime | None = None) -> tuple[dict, bool]:
        """Save a pasted build. Returns (metadata, created); created is False when this exact
        build was already saved, in which case the earlier snapshot is returned unchanged."""
        character = (character or "").strip() or default_character(summary)
        digest = content_hash(xml)
        for existing in self.list(character):
            if existing.get("content_hash") == digest:
                return existing, False
        snap_id = uuid.uuid4().hex[:16]
        meta = {
            "id": snap_id,
            "content_hash": digest,
            "character": character,
            "taken_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "level": summary.get("level"),
            "class": summary.get("class"),
            "ascendancy": summary.get("ascendancy"),
            "main_skill": summary.get("mainSkill"),
            "summary": summary,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        meta_path, xml_path = self._paths(snap_id)
        # XML first, metadata last: a snapshot only exists once its .json does, so a crash
        # between the two writes leaves an orphan .xml.gz rather than a snapshot without XML.
        _atomic_write(xml_path, gzip.compress(xml.encode("utf-8")))
        _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        return meta, True

    def get(self, snap_id: str) -> dict | None:
        if not SNAPSHOT_ID_RE.match(snap_id):
            return None
        meta_path, _ = self._paths(snap_id)
        try:
            return json.loads(meta_path.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning("unreadable snapshot %s: %s", snap_id, e)
            return None

    def xml(self, snap_id: str) -> str | None:
        if not SNAPSHOT_ID_RE.match(snap_id):
            return None
        try:
            return gzip.decompress(self._paths(snap_id)[1].read_bytes()).decode("utf-8")
        except FileNotFoundError:
            return None

    def list(self, character: str | None = None) -> list[dict]:
        """All snapshots (or one character's), oldest first."""
        if not self.dir.exists():
            return []
        snaps = []
        for path in self.dir.glob("*.json"):
            if (meta := self.get(path.stem)) and (character is None or meta["character"] == character):
                snaps.append(meta)
        return sorted(snaps, key=lambda m: (m["taken_at"], m["id"]))

    def characters(self) -> list[dict]:
        """One row per character: snapshot count, first/last paste, latest level."""
        rows: dict[str, dict] = {}
        for m in self.list():
            row = rows.setdefault(m["character"], {"character": m["character"], "snapshots": 0,
                                                   "first_taken_at": m["taken_at"]})
            row["snapshots"] += 1
            row.update(last_taken_at=m["taken_at"], level=m["level"],
                       **{"class": m["class"], "ascendancy": m["ascendancy"]})
        return sorted(rows.values(), key=lambda r: r["last_taken_at"], reverse=True)

    def rename(self, snap_id: str, character: str) -> dict | None:
        """Move a snapshot to another character (e.g. after pasting without a name)."""
        if not (meta := self.get(snap_id)):
            return None
        meta["character"] = character.strip()
        _atomic_write(self._paths(snap_id)[0], json.dumps(meta).encode("utf-8"))
        return meta

    def delete(self, snap_id: str) -> bool:
        if not SNAPSHOT_ID_RE.match(snap_id):
            return False
        meta_path, xml_path = self._paths(snap_id)
        existed = meta_path.exists()
        meta_path.unlink(missing_ok=True)
        xml_path.unlink(missing_ok=True)
        return existed


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def listing(meta: dict) -> dict[str, Any]:
    """A snapshot without its full summary, plus its sidebar stats as a flat {stat: value}
    map: compact enough to list a character's whole history in one response."""
    out = {k: v for k, v in meta.items() if k != "summary"}
    stats = (meta.get("summary") or {}).get("stats") or {}
    out["stats"] = {row["stat"]: row["value"] for row in stats.get("player", [])}
    return out
