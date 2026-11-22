"""HTTP API over the headless Path of Building engine.

Each loaded build is a session backed by its own engine worker process. Workers
are capped (POB_MAX_WORKERS); when the cap is hit the least-recently-used worker
is shut down, but its build XML is kept, so the next request to that build
transparently restarts a worker and reloads it (~3s).

Run:  app/api/.venv/bin/uvicorn main:app --app-dir app/api --reload
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field, model_validator

import buildcode
import settings
from agent import Agent, ToolEvent
from engine import EngineCrashed, EngineError, EngineWorker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("pob.api")

MAX_WORKERS = int(os.getenv("POB_MAX_WORKERS", "4"))


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, build_id: str):
        self.id = build_id
        self.worker = EngineWorker()
        self.last_used = time.monotonic()
        self.agent: Agent | None = None  # created on first chat message
        self.chat_lock = asyncio.Lock()  # one question at a time per build


class SessionManager:
    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    def get(self, build_id: str) -> Session:
        session = self.sessions.get(build_id)
        if not session:
            raise HTTPException(404, f"no build with id {build_id}")
        session.last_used = time.monotonic()
        return session

    async def create(self) -> Session:
        session = Session(uuid.uuid4().hex[:12])
        self.sessions[session.id] = session
        return session

    async def make_room(self, keep: Session) -> None:
        """Shut down LRU workers (keeping their XML) so at most max_workers run."""
        async with self._lock:
            live = [s for s in self.sessions.values() if s.worker.alive and s is not keep]
            excess = len(live) + 1 - self.max_workers
            for s in sorted(live, key=lambda s: s.last_used)[:max(excess, 0)]:
                log.info("parking worker for build %s", s.id)
                await s.worker.close()

    async def call(self, session: Session, cmd: str, **args: Any) -> Any:
        if not session.worker.alive:
            await self.make_room(keep=session)
        return await session.worker.call(cmd, **args)

    async def delete(self, build_id: str) -> None:
        session = self.sessions.pop(build_id, None)
        if session:
            await session.worker.close()

    async def close_all(self) -> None:
        await asyncio.gather(*(s.worker.close() for s in self.sessions.values()))


sessions = SessionManager(MAX_WORKERS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await sessions.close_all()


app = FastAPI(title="PoB Engine API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(EngineError)
async def engine_error(_: Request, exc: EngineError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(EngineCrashed)
async def engine_crashed(_: Request, exc: EngineCrashed):
    log.error("engine crashed: %s", exc)
    return JSONResponse(status_code=503, content={"detail": "engine crashed; retry the request"})


@app.exception_handler(settings.LLMNotConfigured)
async def llm_not_configured(_: Request, exc: settings.LLMNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(buildcode.InvalidBuildCode)
async def bad_code(_: Request, exc: buildcode.InvalidBuildCode):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class LoadBuild(BaseModel):
    code: str | None = Field(None, description="PoB share code")
    xml: str | None = Field(None, description="Raw PoB build XML")
    name: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.code) == bool(self.xml):
            raise ValueError("provide exactly one of 'code' or 'xml'")
        return self


class NodeChange(BaseModel):
    add: list[str | int] = []
    remove: list[str | int] = []


class ItemChange(BaseModel):
    slot: str = Field(..., examples=["Helmet", "Ring 1", "Weapon 1"])
    item: str = Field(..., description="Item text as copied in-game (Ctrl+C)")


class ConfigValue(BaseModel):
    value: bool | int | float | str | None


class ChatMessage(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


def make_agent(session: Session) -> Agent:
    """Agent whose engine calls go through the session manager (so parked workers revive)."""
    settings.require_llm_configured()

    async def engine_call(cmd: str, **args: Any) -> Any:
        return await sessions.call(session, cmd, **args)

    return Agent(engine_call=engine_call)


class MainSkill(BaseModel):
    index: int = Field(..., ge=1, description="Skill group index from the summary")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "ok": True,
        "chat_configured": not settings.missing_llm_settings(),
        "builds": len(sessions.sessions),
        "running_workers": sum(s.worker.alive for s in sessions.sessions.values()),
        "max_workers": MAX_WORKERS,
    }


@app.post("/builds", status_code=201)
async def load_build(body: LoadBuild):
    text = body.code or ""
    xml = body.xml or (text if buildcode.looks_like_xml(text) else buildcode.decode(text))
    session = await sessions.create()
    try:
        summary = await sessions.call(session, "load_xml", xml=xml, name=body.name or "build")
    except Exception:
        await sessions.delete(session.id)
        raise
    return {"build_id": session.id, "summary": summary}


@app.get("/builds")
async def list_builds():
    return [
        {"build_id": s.id, "name": s.worker.build_name, "running": s.worker.alive}
        for s in sessions.sessions.values()
    ]


@app.get("/builds/{build_id}")
async def summary(build_id: str):
    return await sessions.call(sessions.get(build_id), "summary")


@app.delete("/builds/{build_id}", status_code=204)
async def delete_build(build_id: str):
    sessions.get(build_id)
    await sessions.delete(build_id)


@app.get("/builds/{build_id}/stats")
async def stats(build_id: str, keys: list[str] | None = Query(None, description="Raw output keys, e.g. Life")):
    return await sessions.call(sessions.get(build_id), "stats", **({"keys": keys} if keys else {}))


@app.get("/builds/{build_id}/export")
async def export(build_id: str, include_xml: bool = False):
    result = await sessions.call(sessions.get(build_id), "export_xml")
    out = {"code": buildcode.encode(result["xml"])}
    if include_xml:
        out["xml"] = result["xml"]
    return out


@app.put("/builds/{build_id}/main-skill")
async def set_main_skill(build_id: str, body: MainSkill):
    return await sessions.call(sessions.get(build_id), "set_main_skill", index=body.index)


# -- passive tree --

@app.get("/builds/{build_id}/nodes")
async def search_nodes(
    build_id: str,
    query: str = "",
    types: list[str] | None = Query(None, description="Normal, Notable, Keystone, Mastery, Socket"),
    reachable_only: bool = False,
    allocated: bool | None = None,
    limit: int = Query(25, ge=1, le=500),
):
    args: dict[str, Any] = {"query": query, "reachable_only": reachable_only, "limit": limit}
    if types:
        args["types"] = types
    if allocated is not None:
        args["allocated"] = allocated
    return await sessions.call(sessions.get(build_id), "search_nodes", **args)


class RankNodes(BaseModel):
    stat: str = "TotalDPS"
    query: str = ""
    types: list[str] | None = None
    max_cost: int = Field(5, ge=1, le=15)
    candidates: list[str | int] | None = None
    limit: int = Field(10, ge=1, le=100)
    also: list[str] | None = None


@app.post("/builds/{build_id}/nodes/rank")
async def rank_nodes(build_id: str, body: RankNodes):
    args = {k: v for k, v in body.model_dump().items() if v not in (None, "", [])}
    return await sessions.call(sessions.get(build_id), "rank_nodes", **args)


@app.post("/builds/{build_id}/nodes/compare")
async def compare_nodes(build_id: str, body: NodeChange):
    return await sessions.call(sessions.get(build_id), "compare_nodes", add=body.add, remove=body.remove)


@app.post("/builds/{build_id}/nodes/apply")
async def apply_nodes(build_id: str, body: NodeChange):
    return await sessions.call(sessions.get(build_id), "apply_nodes", add=body.add, remove=body.remove)


# -- items --

@app.get("/builds/{build_id}/items")
async def item_details(build_id: str, slot: str | None = None):
    return await sessions.call(sessions.get(build_id), "item_details", **({"slot": slot} if slot else {}))


@app.post("/builds/{build_id}/items/compare")
async def compare_item(build_id: str, body: ItemChange):
    return await sessions.call(sessions.get(build_id), "compare_item", slot=body.slot, item=body.item)


@app.post("/builds/{build_id}/items/equip")
async def equip_item(build_id: str, body: ItemChange):
    return await sessions.call(sessions.get(build_id), "equip_item", slot=body.slot, item=body.item)


# -- configuration --

@app.get("/builds/{build_id}/config")
async def list_config(build_id: str, query: str = "", set_only: bool = False):
    return await sessions.call(sessions.get(build_id), "list_config", query=query, set_only=set_only)


@app.put("/builds/{build_id}/config/{var}")
async def set_config(build_id: str, var: str, body: ConfigValue):
    return await sessions.call(sessions.get(build_id), "set_config", var=var, value=body.value)


# -- diagnostics --

@app.post("/builds/{build_id}/benchmark")
async def benchmark(build_id: str, samples: int = Query(50, ge=1, le=1000)):
    return await sessions.call(sessions.get(build_id), "benchmark", samples=samples)


# -- chat --

@app.post("/builds/{build_id}/chat")
async def chat(build_id: str, body: ChatMessage):
    """Ask the build agent a question. The conversation continues until reset."""
    session = sessions.get(build_id)
    async with session.chat_lock:
        if session.agent is None:
            session.agent = make_agent(session)
        agent = session.agent
        events: list[ToolEvent] = []
        agent.on_tool = events.append
        tokens_before = agent.tokens_used
        try:
            answer = await agent.ask(body.message)
        except (EngineError, EngineCrashed):
            raise
        except Exception as e:  # model endpoint down, VPN off, quota exhausted...
            log.warning("LLM request failed: %s", e)
            raise HTTPException(502, f"language model request failed: {e}") from e
        return {
            "answer": answer,
            "tool_calls": [{"name": e.name, "args": e.args, "ok": e.ok, "summary": e.summary} for e in events],
            "tokens": agent.tokens_used - tokens_before,
            "model": agent.model,
        }


def _tool_json(e: ToolEvent) -> dict:
    return {"name": e.name, "args": e.args, "ok": e.ok, "summary": e.summary}


@app.post("/builds/{build_id}/chat/stream")
async def chat_stream(build_id: str, body: ChatMessage):
    """Like /chat, but streams progress as server-sent events while the agent works:
    `thinking` (a model request started), `tool` (an engine call finished),
    then exactly one of `answer` or `error`."""
    session = sessions.get(build_id)
    queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()

    async def run() -> None:
        async with session.chat_lock:
            try:
                if session.agent is None:
                    session.agent = make_agent(session)
                agent = session.agent
                tokens_before = agent.tokens_used
                tools: list[ToolEvent] = []

                def on_tool(e: ToolEvent) -> None:
                    tools.append(e)
                    queue.put_nowait(("tool", _tool_json(e)))

                agent.on_tool = on_tool
                agent.on_round = lambda n: queue.put_nowait(("thinking", {"round": n}))
                try:
                    answer = await agent.ask(body.message)
                finally:
                    agent.on_tool = agent.on_round = None
                queue.put_nowait(("answer", {
                    "answer": answer,
                    "tool_calls": [_tool_json(e) for e in tools],
                    "tokens": agent.tokens_used - tokens_before,
                    "model": agent.model,
                }))
            except Exception as e:  # LLM down / not configured / engine crash
                log.warning("chat failed: %s", e)
                detail = str(e) if isinstance(e, (EngineError, settings.LLMNotConfigured)) \
                    else f"language model request failed: {e}"
                queue.put_nowait(("error", {"detail": detail}))
            finally:
                queue.put_nowait(None)

    # The agent runs to completion even if the browser disconnects, so the
    # conversation history stays consistent.
    task = asyncio.create_task(run())

    async def events():
        while (item := await queue.get()) is not None:
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data)}\n\n"
        await task

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.delete("/builds/{build_id}/chat", status_code=204)
async def reset_chat(build_id: str):
    """Start a fresh conversation (build changes made via set_config are kept)."""
    session = sessions.get(build_id)
    async with session.chat_lock:
        if session.agent:
            session.agent.reset()


# -- web app --
# Serve the built React app (app/web/dist) at / when it exists, e.g. in the Docker image.
# Mounted last so every API route above takes precedence. In development, Vite serves it.
WEB_DIST = Path(os.getenv("WEB_DIST", Path(__file__).resolve().parent.parent / "web" / "dist"))
if (WEB_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
