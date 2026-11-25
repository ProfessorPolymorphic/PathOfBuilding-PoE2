"""Every chat conversation, kept on disk so it can be reread and used to improve the agent.

One JSON Lines file per conversation, appended to as the conversation goes:

    <dir>/<conversation id>.jsonl

The first line describes the conversation (character, the snapshot it was about, model). Each
later line is one question: what was asked, the answer or error, the tool calls the user saw,
and the exact messages the model sent and received for it (tool arguments and full engine
results included), so a bad answer can be traced to the step that went wrong.

Files are only ever appended to. A conversation that was reset just stops; the next question
starts a new file.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("pob.chatlog")

CONVERSATION_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ChatLog:
    def __init__(self, directory: Path):
        self.dir = directory

    def _path(self, conv_id: str) -> Path:
        return self.dir / f"{conv_id}.jsonl"

    def _append(self, conv_id: str, record: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._path(conv_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def start(self, *, character: str | None, snapshot_id: str | None, build_id: str,
              model: str | None) -> str:
        conv_id = uuid.uuid4().hex[:16]
        self._append(conv_id, {"type": "start", "id": conv_id, "started_at": _now(),
                               "character": character, "snapshot_id": snapshot_id,
                               "build_id": build_id, "model": model})
        return conv_id

    def turn(self, conv_id: str, *, question: str, answer: str | None = None,
             error: str | None = None, tool_calls: list[dict] | None = None,
             messages: list[dict] | None = None, tokens: int | None = None,
             duration_ms: int | None = None) -> None:
        self._append(conv_id, {"type": "turn", "at": _now(), "question": question,
                               "answer": answer, "error": error, "tool_calls": tool_calls or [],
                               "tokens": tokens, "duration_ms": duration_ms,
                               "messages": messages or []})

    def read(self, conv_id: str) -> list[dict] | None:
        """All records of a conversation, or None if there is no such conversation.
        A torn last line (crash mid-write) is skipped rather than failing the whole file."""
        if not CONVERSATION_ID_RE.match(conv_id):
            return None
        try:
            lines = self._path(conv_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                log.warning("skipping unreadable line in conversation %s", conv_id)
        return records

    def get(self, conv_id: str) -> dict | None:
        """A conversation for display: its start record plus turns, without the raw
        model messages (those stay in the file for debugging)."""
        records = self.read(conv_id)
        if not records or records[0].get("type") != "start":
            return None
        turns = [{k: v for k, v in r.items() if k not in ("type", "messages")}
                 for r in records[1:] if r.get("type") == "turn"]
        return {**{k: v for k, v in records[0].items() if k != "type"}, "turns": turns}

    def list(self, character: str | None = None) -> list[dict[str, Any]]:
        """Conversations (optionally one character's), newest first, with their first
        question as a title. Conversations with no turns are left out."""
        if not self.dir.exists():
            return []
        out = []
        for path in self.dir.glob("*.jsonl"):
            conv = self.get(path.stem)
            if not conv or not conv["turns"]:
                continue
            if character is not None and conv.get("character") != character:
                continue
            out.append({k: v for k, v in conv.items() if k != "turns"}
                       | {"title": conv["turns"][0]["question"], "turns": len(conv["turns"]),
                          "last_at": conv["turns"][-1]["at"]})
        return sorted(out, key=lambda c: c["started_at"], reverse=True)

    def rename_character(self, snapshot_id: str, character: str) -> None:
        """Keep conversations with their snapshot when it's moved to another character.
        This rewrites only the start line, so the file stays append-only in practice."""
        if not self.dir.exists():
            return
        for path in self.dir.glob("*.jsonl"):
            lines = path.read_text(encoding="utf-8").splitlines()
            try:
                start = json.loads(lines[0])
            except (IndexError, ValueError):
                continue
            if start.get("snapshot_id") == snapshot_id and start.get("character") != character:
                start["character"] = character
                lines[0] = json.dumps(start, default=str)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(path)
