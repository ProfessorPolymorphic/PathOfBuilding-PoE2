"""Async client for the headless PoB Lua worker (app/engine/worker.lua).

Each EngineWorker owns one LuaJIT subprocess holding one loaded build. The worker
is single-threaded, so calls are serialised with a lock. If the process dies, the
next call restarts it and reloads the last known build XML, so a crash costs one
failed request rather than the user's session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
POB_SH = ENGINE_DIR / "pob.sh"

# Commands that change the build; after these we snapshot the XML for crash recovery.
MUTATING = {"load_xml", "apply_nodes", "equip_item", "set_config", "set_main_skill"}


class EngineError(Exception):
    """The engine rejected a command (bad input, unknown node, ...). Safe to show users."""


class EngineCrashed(Exception):
    """The worker process died or produced garbage."""


class EngineWorker:
    def __init__(self, call_timeout: float = 60.0, boot_timeout: float = 60.0):
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._call_timeout = call_timeout
        self._boot_timeout = boot_timeout
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._stderr_task: asyncio.Task | None = None
        self.xml_snapshot: str | None = None
        self.build_name: str = "build"

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            str(POB_SH), "worker.lua",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=64 * 1024 * 1024,  # build XML responses can be large
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            hello = await asyncio.wait_for(self._read_message(), self._boot_timeout)
        except Exception:
            await self.close()
            raise
        if not hello.get("ok"):
            await self.close()
            raise EngineCrashed(hello.get("error", "engine failed to start"))
        log.info("engine worker ready (boot %.2fs)", hello["result"].get("boot_seconds", 0))

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for raw in self._proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            self._stderr_tail.append(line)
            log.debug("engine: %s", line)

    # -- protocol --------------------------------------------------------

    async def _read_message(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        raw = await self._proc.stdout.readline()
        if not raw:
            tail = "\n".join(self._stderr_tail)
            raise EngineCrashed(f"engine exited unexpectedly\n{tail}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise EngineCrashed(f"bad engine output: {raw[:200]!r}") from e

    async def _send(self, cmd: str, args: dict[str, Any]) -> Any:
        assert self._proc and self._proc.stdin
        self._next_id += 1
        req_id = self._next_id
        self._proc.stdin.write((json.dumps({"id": req_id, "cmd": cmd, "args": args}) + "\n").encode())
        await self._proc.stdin.drain()
        msg = await asyncio.wait_for(self._read_message(), self._call_timeout)
        if msg.get("id") != req_id:
            raise EngineCrashed(f"response id mismatch: sent {req_id}, got {msg.get('id')}")
        if not msg.get("ok"):
            raise EngineError(msg.get("error", "unknown engine error"))
        return msg.get("result")

    async def call(self, cmd: str, **args: Any) -> Any:
        """Run one engine command. Restarts the worker (and reloads the build) if it died."""
        async with self._lock:
            if not self.alive:
                await self._restart_and_restore()
            try:
                result = await self._send(cmd, args)
            except (EngineCrashed, asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                # Kill it so the next call gets a fresh worker with the last good build.
                await self.close()
                raise
            if cmd == "load_xml":
                self.xml_snapshot = args["xml"]
                self.build_name = args.get("name") or self.build_name
            elif cmd in MUTATING:
                self.xml_snapshot = (await self._send("export_xml", {}))["xml"]
            return result

    async def _restart_and_restore(self) -> None:
        await self.close()
        await self.start()
        if self.xml_snapshot:
            log.warning("engine restarted; restoring last build snapshot")
            await self._send("load_xml", {"xml": self.xml_snapshot, "name": self.build_name})
