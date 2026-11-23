# app/ — custom interface on top of the PoB engine (Path of Exile 2)

Everything outside `src/` that we add lives here, so pulling upstream PoB updates stays conflict-free.

```
web/ (React)  ──HTTP──▶  api/ (FastAPI + LLM agent)  ──JSON lines──▶  engine/worker.lua (LuaJIT + PoB headless)
```

**Quickest start:** `docker compose -f app/docker-compose.yml up -d --build`, then open
http://localhost:8000 and paste a PoE2 build code.

## Setup (macOS)

```sh
app/engine/setup.sh                                    # pinned LuaJIT + native Lua modules (once)
python3 -m venv app/api/.venv
app/api/.venv/bin/pip install -r app/api/requirements-dev.txt
```

## Web app (development)

```sh
cd app/web && npm install
npm run dev          # http://localhost:5173, proxies API calls to localhost:8000
```

Needs the API running on :8000 (Docker or uvicorn). `npm run build` writes `web/dist`,
which FastAPI serves at `/` when present; the Docker image builds and serves it.

## Chat (LLM agent)

Configure `app/.env` (copy `app/.env.example`): an OpenAI-compatible endpoint, key and model.
Default is MindRouter (UI network or VPN) with `moonshotai/kimi-k3`.

```sh
app/api/.venv/bin/python app/api/list_models.py kimi             # check key + model IDs
app/api/.venv/bin/python app/api/chat.py                         # chat about the test build
app/api/.venv/bin/python app/api/chat.py --code-file mycode.txt  # ...or your own build
```

Over HTTP: `POST /builds/{id}/chat` with `{"message": "..."}` returns the answer, the tool
calls made and tokens used; the conversation continues per build until
`DELETE /builds/{id}/chat`. Without LLM settings the endpoint answers 503 and the rest of
the API works normally.

`api/agent.py` gives the model engine tools (summary, stats, item details, passive
search/rank/compare, item compare, config, main skill). The model chooses tools and
explains; all numbers come from the engine. Tool calls are shown dimmed in the chat.

## Dashboard and change preview card

Pasting a build fills a dashboard beside the chat. Every candidate change is shown as a
change preview card: the same core stats every time (Total DPS; Life, ES, Spirit; Armour,
Evasion, resistances; effective HP, max hit by type), then any other sidebar stat that
changed. The engine builds the card (`buildCard` in `engine/worker.lua`), so its numbers
never pass through the LLM. `compare_item` and `compare_nodes` return one as `card`, and the
chat shows the cards for every what-if the agent tested. The model doesn't see the cards.

```
GET /builds/{id}/card                      core stats of the build as it is
GET /builds/{id}/dashboard[?download=true] baseline card + every notable/keystone within
                                           5 points tested with its path, each with a card
```

The dashboard response is cached per build until the build changes (the first request
takes about 4s). `?download=true` returns it as a file, for sketching views in Observable.

## Hit distributions

PoB reports one average per hit. `hit_distribution` in `engine/worker.lua` recovers the random
draws behind that average from the same calculation: hit or miss, crit or not, each damage
type's uniform roll (after enemy resistances and armour), lucky rolls, double/triple damage.
`api/distribution.py` samples them. The model's exact mean equals PoB's AverageDamage (tested).

```
POST /builds/{id}/distribution
  {"skill": "Shred", "samples": 20000,
   "scenarios": [{"name": "As configured"},
                 {"name": "Surrounded", "config": {"conditionSurrounded": true}}]}
```

Scenario config overrides apply to that calculation only; the build isn't changed. Each
scenario returns PoB's averages, the extracted model, a summary (quantiles, miss/crit share)
and the samples as columns (damage, outcome, multiplier, hand, and damage by type unless
`by_type` is false). The Damage distribution tab charts it (pick a skill, add scenarios from
any on/off Configuration setting); "Download the samples" saves the full data.

Inherits PoB's simplifications: armour reduction uses the average hit, and effects PoB
averages as uptime (rather than as a chance per hit) stay averaged.

### Against enemy defences

`hit_sweep` in `engine/worker.lua` recalculates a skill at each value of one enemy defence
(armour, evasion, a resistance, physical damage reduction, block, enemy level), everything
else as configured. Like PoB's tooltip calculator it builds the player and enemy once and adds
only the defence per value, so a 25-value sweep takes well under a second; each value equals a
full recalculation (tested). Enemy level changes the base setup, so it takes the full path.

```
GET  /defences                    what can be swept, with labels and default ranges
POST /builds/{id}/sweep
  {"skill": "Shred", "defence": "enemyArmour", "lo": 0, "hi": 30000, "steps": 25}
```

Each row is sampled and binned on one shared damage axis (share of attempts per bin, misses
counted apart); the build's current value is always one of the rows. The Against enemy
defences tab draws it as a density map: damage across, the defence up, with the distribution
at your config on top and the average per attack on the right.

## Snapshots (build history)

Every build loaded through `POST /builds` is also saved as a snapshot: the XML as pasted,
the paste time, level and the engine summary. Snapshots never change afterwards; what-ifs and
equips only touch the working copy. They're grouped by character: pass `"character": "Name"`
when loading, otherwise the class is used. Pasting the same build again for the same character
doesn't add a duplicate (compared by a fingerprint that ignores PoB's cached stats and
indentation, but not line breaks, which separate item mods).

```
GET    /characters                    characters with snapshot counts and latest level
GET    /snapshots?character=Druid     history, oldest first, sidebar stats as {stat: value}
GET    /snapshots/{id}?include_xml=1  one snapshot with full summary (and XML)
PATCH  /snapshots/{id}                {"character": "..."} to regroup it
POST   /snapshots/{id}/load           reopen it as a working build
DELETE /snapshots/{id}
```

Stored in `POB_SNAPSHOT_DIR` (default `app/.state/snapshots`; `/data/snapshots` in Docker).

## Docker

```sh
docker compose -f app/docker-compose.yml up -d --build                          # API on http://localhost:8000/docs
docker compose -f app/docker-compose.yml run --rm --no-deps api pytest app/api  # tests inside the image
docker compose -f app/docker-compose.yml down
```

The container reads `app/.env` for the LLM settings. Its network is pinned to
`10.20.60.0/24`: Docker's default 172.x subnets can swallow MindRouter's campus/VPN address
(172.27.x), making it unreachable from the container.

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

- **Worker protocol** (`engine/worker.lua`): one JSON request per line on stdin, one response per line on stdout. PoB logs go to stderr. Commands: `load_xml`, `summary`, `stats`, `export_xml`, `item_details`, `search_nodes`, `rank_nodes`, `compare_nodes`, `apply_nodes`, `compare_item`, `equip_item`, `list_config`, `set_config`, `set_main_skill`, `hit_distribution`, `hit_sweep`, `sweep_current`, `benchmark`.
- `compare_*` commands are what-ifs computed with PoB's own misc calculator (the one behind its tooltips); they don't modify the build. `apply_*`/`equip_*`/`set_*` do.
- Share codes are decoded in Python (`api/buildcode.py`) because headless PoB stubs out `Inflate`/`Deflate`.
- One worker holds one build, boots in ~1.3s, loads a build in ~1.3s, uses ~400 MB, and evaluates a single what-if in ~24 ms. The API caps running workers (`POB_MAX_WORKERS`, default 4) and parks the least recently used ones; a parked build reloads from its XML snapshot on next use. A crashed worker is restarted the same way.
- LuaJIT is pinned to the commit upstream CI uses (see repo `Dockerfile`): newer LuaJIT rejects the `#@` first line of PoB's entry files.
