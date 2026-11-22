# app/ — custom interface on top of the PoB engine (Path of Exile 2)

Everything outside `src/` that we add lives here, so pulling upstream PoB updates stays conflict-free.

```
React (todo)  ──HTTP──▶  api/ (FastAPI)  ──JSON lines──▶  engine/worker.lua (LuaJIT + PoB headless)
```

## Setup (macOS)

```sh
app/engine/setup.sh                                    # pinned LuaJIT + native Lua modules (once)
python3 -m venv app/api/.venv
app/api/.venv/bin/pip install -r app/api/requirements-dev.txt
```

## Chat (LLM agent)

Configure `app/.env` (copy `app/.env.example`): an OpenAI-compatible endpoint, key and model.
Default is MindRouter (UI network or VPN) with `moonshotai/kimi-k3`.

```sh
app/api/.venv/bin/python app/api/list_models.py kimi             # check key + model IDs
app/api/.venv/bin/python app/api/chat.py                         # chat about the test build
app/api/.venv/bin/python app/api/chat.py --code-file mycode.txt  # ...or your own build
```

`api/agent.py` gives the model engine tools (summary, stats, item details, passive
search/rank/compare, item compare, config, main skill). The model chooses tools and
explains; all numbers come from the engine. Tool calls are shown dimmed in the chat.

## Docker

```sh
docker compose -f app/docker-compose.yml up -d --build                          # API on http://localhost:8000/docs
docker compose -f app/docker-compose.yml run --rm --no-deps api pytest app/api  # tests inside the image
docker compose -f app/docker-compose.yml down
```

The image (~330 MB) builds its own pinned LuaJIT and native modules and leaves out
GUI textures (`app/Dockerfile.dockerignore`); the in-image test run confirms the
engine doesn't need them.

## Run natively

```sh
app/api/.venv/bin/uvicorn main:app --app-dir app/api --reload   # http://localhost:8000/docs
```

## Test

```sh
app/api/.venv/bin/pytest app/api        # API + engine integration tests (~30s, uses tests/fixtures/shaman.xml)
app/engine/test.sh                      # upstream PoB busted suite (~60s)
```

## Engine notes

- **Worker protocol** (`engine/worker.lua`): one JSON request per line on stdin, one response per line on stdout. PoB logs go to stderr. Commands: `load_xml`, `summary`, `stats`, `export_xml`, `item_details`, `search_nodes`, `rank_nodes`, `compare_nodes`, `apply_nodes`, `compare_item`, `equip_item`, `list_config`, `set_config`, `set_main_skill`, `benchmark`.
- `compare_*` commands are what-ifs computed with PoB's own misc calculator (the one behind its tooltips); they don't modify the build. `apply_*`/`equip_*`/`set_*` do.
- Share codes are decoded in Python (`api/buildcode.py`) because headless PoB stubs out `Inflate`/`Deflate`.
- One worker holds one build, boots in ~1.3s, loads a build in ~1.3s, uses ~400 MB, and evaluates a single what-if in ~13 ms (PoE1) / ~23 ms (PoE2). The API caps running workers (`POB_MAX_WORKERS`, default 4) and parks the least recently used ones; a parked build reloads from its XML snapshot on next use. A crashed worker is restarted the same way.
- `worker.lua` handles both PoE1 and PoE2 PoB (e.g. skill flags live on `mainSkill` in PoE1 but on `activeEffect.statSet` in PoE2).
- LuaJIT is pinned to the commit upstream CI uses (see repo `Dockerfile`): newer LuaJIT rejects the `#@` first line of PoB's entry files.
