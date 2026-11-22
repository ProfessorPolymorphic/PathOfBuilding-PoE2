"""List models available to your LLM key and whether each supports tool calling.

    app/api/.venv/bin/python app/api/list_models.py [filter]
"""

from __future__ import annotations

import sys

import httpx

import settings

if not settings.LLM_API_KEY or settings.LLM_API_KEY.startswith("mr2_paste"):
    raise SystemExit(f"Set LLM_API_KEY in {settings.ENV_FILE} first (see app/.env.example)")

flt = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
try:
    r = httpx.get(f"{settings.LLM_BASE_URL.rstrip('/')}/models",
                  headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"}, timeout=20)
except httpx.ConnectError as e:
    raise SystemExit(f"Can't reach {settings.LLM_BASE_URL}: {e}\n(Off campus? Connect to the UI VPN.)")
if r.status_code != 200:
    raise SystemExit(f"{r.status_code} from {r.url}: {r.text[:300]}")

models = r.json().get("data", [])
rows = []
for m in models:
    mid = m.get("id", "")
    if flt and flt not in mid.lower():
        continue
    caps = m.get("capabilities") or {}
    tools = caps.get("tools")
    ctx = m.get("context_length") or m.get("max_context") or caps.get("context_length") or ""
    rows.append((mid, "yes" if tools else ("no" if tools is False else "?"), str(ctx)))

width = max((len(r[0]) for r in rows), default=10)
print(f"{'MODEL'.ljust(width)}  TOOLS  CONTEXT")
for mid, tools, ctx in sorted(rows):
    print(f"{mid.ljust(width)}  {tools.ljust(5)}  {ctx}")
print(f"\n{len(rows)} of {len(models)} models shown. Kimi candidates: "
      f"{[r[0] for r in rows if 'kimi' in r[0].lower()] or 'none found'}")
