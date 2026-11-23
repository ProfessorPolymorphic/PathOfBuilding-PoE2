"""Answer-quality evaluation for the build agent.

Runs every case in cases.yaml against the real engine and the configured LLM, scores
each answer, and writes a JSON report to app/eval/results/.

    app/api/.venv/bin/python app/eval/run_eval.py                 # all cases
    app/api/.venv/bin/python app/eval/run_eval.py --only cold     # cases whose id contains "cold"
    app/api/.venv/bin/python app/eval/run_eval.py --model qwen/qwen3.5-122b

Scoring:
  numbers          engine values the answer must contain (parsed from the text, ~1.5% tolerance)
  claims           yes/no statements judged by the Decisions API (CLEF) against the answer
  tools_any        the agent must use at least one of the listed tools
  config_restored  the config must be unchanged after the answer
A case passes when every check passes. Judge failures are reported as "unjudged",
not as passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "api"))
# --agent-dir DIR: load agent.py from DIR instead (e.g. an older version, for A/B comparison).
if "--agent-dir" in sys.argv:
    sys.path.insert(0, sys.argv[sys.argv.index("--agent-dir") + 1])

import settings  # noqa: E402
from agent import Agent, ToolEvent  # noqa: E402
from engine import EngineError, EngineWorker  # noqa: E402

FIXTURES = HERE.parent / "api" / "tests" / "fixtures"
RESULTS = HERE / "results"
JUDGE_MODEL = "clef"
NUMBER_RE = re.compile(r"(?<![\w.])[-−]?\d[\d,]*(?:\.\d+)?\s*[kKmM]?(?![\w])")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUMBER_RE.finditer(text):
        raw = m.group(0).replace(",", "").replace("−", "-").strip()
        mult = 1.0
        if raw[-1] in "kK":
            mult, raw = 1e3, raw[:-1]
        elif raw[-1] in "mM":
            mult, raw = 1e6, raw[:-1]
        try:
            out.append(float(raw.strip()) * mult)
        except ValueError:
            pass
    return out


def contains_number(text: str, value: float, tol: float = 0.015) -> bool:
    nums = numbers_in(text)
    slack = max(abs(value) * tol, 0.5 if abs(value) >= 10 else 0.05)
    return any(abs(n - value) <= slack or abs(abs(n) - abs(value)) <= slack for n in nums)


def ungrounded_numbers(answer: str, sources: list[str]) -> list[str]:
    """Numbers in the answer that don't appear (within rounding) in anything the model was
    given: the question, the build snapshot, or tool results. Small integers (< 10) are
    ignored since they're usually list positions, counts or levels."""
    pool: list[float] = []
    for text in sources:
        pool.extend(numbers_in(text))
    known = set()
    for v in pool:
        # as given, as a percentage/fraction, negated, and a multiplier as a percent (1.62 -> 62%)
        known.update({v, v * 100, v / 100, -v, round((v - 1) * 100, 6)})
    # Simple arithmetic on two engine values (totals, differences) is legitimate, not invented.
    distinct = sorted({round(v, 4) for v in pool if abs(v) >= 1})[:600]
    for i, a in enumerate(distinct):
        for b in distinct[i + 1:]:
            known.update({a + b, abs(a - b)})
    def grounded(n: float) -> bool:
        tol = max(abs(n) * 0.012, 0.06)
        return any(abs(n - k) <= tol for k in known)
    bad = []
    for m in NUMBER_RE.finditer(answer):
        token = m.group(0).strip()
        vals = numbers_in(token)
        if not vals:
            continue
        n = vals[0]
        if abs(n) < 10 and float(n).is_integer():
            continue
        if not grounded(n):
            bad.append(token)
    return bad


@dataclass
class CheckResult:
    kind: str
    description: str
    passed: bool | None  # None = could not be judged
    detail: str = ""


@dataclass
class CaseResult:
    id: str
    question: str
    answer: str = ""
    tools: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    tokens: int = 0
    checks: list[CheckResult] = field(default_factory=list)
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)


async def resolve_number(spec: dict, call) -> tuple[float, str]:
    """Turn a number spec into (expected value, description), using the engine."""
    if "value" in spec:
        return float(spec["value"]), f"literal {spec['value']}"
    if "compare_nodes" in spec:
        r = await call("compare_nodes", **spec["compare_nodes"])
        stat = spec.get("stat", "TotalDPS")
        ch = next((c for c in r["changes"]["player"] if c["stat"] == stat), None)
        if ch is None:
            raise ValueError(f"{stat} doesn't change for {spec['compare_nodes']}")
        field_ = spec.get("field", "delta")
        return float(ch[field_]), f"{stat} {field_} for adding {spec['compare_nodes'].get('add')}"
    if "compare_item" in spec:
        r = await call("compare_item", **spec["compare_item"])
        stat = spec["stat"]
        ch = next((c for c in r["changes"]["player"] if c["stat"] == stat), None)
        if ch is None:
            raise ValueError(f"{stat} doesn't change for that item")
        field_ = spec.get("field", "delta")
        return float(ch[field_]), f"{stat} {field_} with the candidate item"
    if "support_contributions" in spec:
        r = await call("support_contributions", **spec["support_contributions"])
        row = next((x for x in r["supports"] if x["support"] == spec["support"]), None)
        change = row and (row.get("changes") or {}).get(r["primaryStat"])
        if not change:
            raise ValueError(f"{spec['support']} has no damage contribution in {spec['support_contributions']}")
        return float(change[spec.get("field", "lost")]), f"{r['primaryStat']} lost without {spec['support']}"
    stat = spec["stat"]
    r = await call("stats", keys=[stat])
    v = r[stat]
    if not isinstance(v, (int, float)):
        raise ValueError(f"stat {stat} is {v!r}")
    return float(v), f"{stat} = {round(v, 2)}"


async def resolve_claim(claim: dict, call) -> str:
    text = claim["text"]
    if "rank_top" in claim:
        r = await call("rank_nodes", **claim["rank_top"], limit=1)
        if not r["results"]:
            raise ValueError(f"rank_nodes found no candidates for {claim['rank_top']}")
        text = text.replace("{top}", r["results"][0]["name"])
    return text


async def judge(http: httpx.AsyncClient, question: str, answer: str, claims: list[str]) -> list[float | None]:
    """Probability that the answer does each claim, via the Decisions API."""
    if not claims:
        return []
    url = settings.LLM_BASE_URL.rstrip("/").rsplit("/v1", 1)[0] + "/v1/systemone"
    payload = {
        "model": JUDGE_MODEL,
        "state": f"A player asked a Path of Exile 2 build assistant:\n{question}\n\nThe assistant answered:\n{answer}",
        "questions": {f"c{i}": {"type": "noul", "instructions": f"Does the assistant's answer {c}?"}
                      for i, c in enumerate(claims)},
    }
    try:
        r = await http.post(url, json=payload, headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"}, timeout=90)
        r.raise_for_status()
        answers = r.json()["answers"]
        return [answers[f"c{i}"]["noul"] for i in range(len(claims))]
    except Exception as e:  # judge unavailable: report as unjudged, never as pass
        print(f"    judge failed: {e}", file=sys.stderr)
        return [None] * len(claims)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_case(case: dict, global_claims: list[dict], worker: EngineWorker, http: httpx.AsyncClient,
                   model: str | None) -> CaseResult:
    res = CaseResult(id=case["id"], question=case["question"].strip())
    xml = (FIXTURES / case.get("build", "shaman.xml")).read_text()
    try:
        await worker.call("load_xml", xml=xml, name=case["id"])
    except Exception as e:  # a stuck/crashed worker shouldn't end the whole run
        await worker.close()  # the next call starts a fresh worker
        res.error = f"engine failed to load the build: {e!r}"
        return res

    # Expectations come from the engine *before* the agent can change anything.
    try:
        expected_numbers = [await resolve_number(n, worker.call) for n in case.get("numbers", [])]
        claims = [(await resolve_claim(c, worker.call), bool(c.get("expect", True)))
                  for c in case.get("claims", []) + global_claims]
        config_before = await worker.call("list_config", set_only=True) if case.get("config_restored") else None
    except (EngineError, ValueError, KeyError) as e:
        res.error = f"bad case definition: {e}"
        return res

    events: list[ToolEvent] = []
    agent = Agent(engine_call=worker.call, on_tool=events.append, **({"model": model} if model else {}))
    t0 = time.monotonic()
    try:
        res.answer = await agent.ask(res.question)
    except Exception as e:
        res.error = f"agent failed: {e}"
        return res
    res.seconds = round(time.monotonic() - t0, 1)
    res.tokens = agent.tokens_used
    res.tools = [{"name": e.name, "args": e.args, "ok": e.ok} for e in events]

    # Every number stated should come from something the model actually saw.
    sources = [m.get("content") or "" for m in agent.messages if m.get("role") in ("system", "tool", "user")]
    bad = ungrounded_numbers(res.answer, sources)
    res.checks.append(CheckResult("grounding", "every number comes from the snapshot or a tool result",
                                  not bad, "" if not bad else f"unsupported: {', '.join(bad[:8])}"))

    for value, desc in expected_numbers:
        ok = contains_number(res.answer, value)
        res.checks.append(CheckResult("number", desc, ok, "" if ok else "not found in answer"))

    if case.get("tools_any"):
        used = {e.name for e in events if e.ok}
        ok = bool(used & set(case["tools_any"]))
        res.checks.append(CheckResult("tools", f"used one of {case['tools_any']}", ok,
                                      "" if ok else f"used {sorted(used) or 'none'}"))

    if config_before is not None:
        after = await worker.call("list_config", set_only=True)
        ok = after == config_before
        res.checks.append(CheckResult("config", "config restored afterwards", ok,
                                      "" if ok else "config differs after the answer"))

    probs = await judge(http, res.question, res.answer, [c for c, _ in claims])
    for (text, expect), p in zip(claims, probs):
        verdict = None if p is None else ((p >= 0.5) == expect)
        res.checks.append(CheckResult("claim", f"{'should' if expect else 'must not'}: {text}", verdict,
                                      "unjudged" if p is None else f"p(yes)={p:.2f}"))
    return res


def print_case(r: CaseResult) -> None:
    status = "PASS" if r.passed else ("ERROR" if r.error else "FAIL")
    print(f"\n[{status}] {r.id}  ({r.seconds}s, {r.tokens:,} tokens, tools: {', '.join(t['name'] for t in r.tools) or 'none'})")
    if r.error:
        print(f"    {r.error}")
    for c in r.checks:
        mark = {True: "✓", False: "✗", None: "?"}[c.passed]
        print(f"    {mark} [{c.kind}] {c.description}" + (f"  — {c.detail}" if c.detail and c.passed is not True else ""))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run cases whose id contains this text")
    ap.add_argument("--model", help="override LLM_MODEL for the agent")
    ap.add_argument("--cases", default=str(HERE / "cases.yaml"))
    ap.add_argument("--agent-dir", help="directory holding an alternative agent.py (A/B testing)")
    ap.add_argument("--label", default="", help="added to the report file name")
    ap.add_argument("--repeat", type=int, default=1, help="run each case N times (models vary run to run)")
    args = ap.parse_args()
    settings.require_llm()

    spec = yaml.safe_load(Path(args.cases).read_text())
    only = args.only.split(",") if args.only else None
    cases = [c for c in spec["cases"] if not only or any(o in c["id"] for o in only)]
    cases = [c for c in cases for _ in range(args.repeat)]
    global_claims = spec.get("global_claims", [])
    model = args.model or settings.LLM_MODEL
    print(f"Running {len(cases)} case(s) with {model}; judge: {JUDGE_MODEL}")

    worker = EngineWorker()
    await worker.start()
    results: list[CaseResult] = []
    try:
        async with httpx.AsyncClient() as http:
            for case in cases:
                r = await run_case(case, global_claims, worker, http, args.model)
                print_case(r)
                results.append(r)
    finally:
        await worker.close()

    passed = sum(r.passed for r in results)
    checks = [c for r in results for c in r.checks]
    judged = [c for c in checks if c.passed is not None]
    print(f"\n{passed}/{len(results)} cases passed; "
          f"{sum(bool(c.passed) for c in judged)}/{len(judged)} checks passed"
          f"{f', {len(checks) - len(judged)} unjudged' if len(judged) < len(checks) else ''}; "
          f"{sum(r.tokens for r in results):,} tokens; "
          f"median {sorted(r.seconds for r in results)[len(results) // 2] if results else 0}s per answer")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y%m%d-%H%M%S}-{model.replace('/', '_')}{'-' + args.label if args.label else ''}.json"
    out.write_text(json.dumps({
        "model": model, "judge": JUDGE_MODEL, "label": args.label, "cases": len(results), "passed": passed,
        "results": [{**{k: v for k, v in r.__dict__.items() if k != "checks"},
                     "passed": r.passed, "checks": [c.__dict__ for c in r.checks]} for r in results],
    }, indent=1))
    print(f"Report: {out}")


if __name__ == "__main__":
    asyncio.run(main())
