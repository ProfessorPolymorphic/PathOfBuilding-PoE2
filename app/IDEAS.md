# Ideas (parked)

## CLEF intent routing and answer checking (MindRouter Decisions API)

**Status:** parked 2026-11-22 after one probe. Revisit when the React UI needs to know how to
render answers, or when answer accuracy becomes the bottleneck.

**What it is.** MindRouter's Decisions API (`POST {base}/v1/systemone`, docs:
https://mindrouter.uidaho.edu/documentation#decisions-api) answers typed questions about a text
and returns probabilities, not prose. Question types: `noul` (yes/no probability), `choice`
(custom options, up to 255 on `clef`), `score` (ordered rubric). Default model `clef`
(Cloudflare's 27B decision model); `qwen/qwen3.8-27b` is the alternative. Same API key as chat.
The state text isn't stored or logged. `clef` truncates state at ~16k tokens.

**Probe result** (14 hand-labelled player messages, 8 intents + "too vague?" + damage/defence goal):

| | clef | qwen/qwen3.8-27b |
|---|---|---|
| Intent accuracy | 11/14 | 13/14 |
| Median latency | 0.31 s | 0.55 s |
| "Too vague" scores | sensible (0.66 for "make it better") | uncalibrated (0.77 for clear questions) |
| Goal accuracy | 10/14 | 10/14 |

clef's misses mostly came with low confidence (0.25, 0.27), so confidence is a usable
"don't trust this" signal. 14 cases is too few to conclude anything firm.

Intent labels used: `explain`, `upgrade_tree`, `upgrade_gear`, `compare`, `config`,
`overview`, `outside_engine`, `chitchat`.

**Proposed uses**
1. *Router:* classify each message first (~0.3 s). Confident label -> focused prompt, smaller
   tool set, and a render hint for the UI (ranked table / before-after card / explanation).
   Low confidence or high "too vague" -> ask a clarifying question or use the general prompt.
   `outside_engine` -> answer with an explicit "PoB can't check this" caveat.
2. *Grounding check:* after the agent answers, ask clef whether the answer states numbers or
   mechanics not supported by the tool results. Targets the errors seen in testing (an invented
   "Pinnacle boss" detail; a resistance formula stated as fact).

**If resumed:** build a ~50-question labelled eval set in the repo first, then add the router
with a confidence threshold and fallback, then test the grounding check against known bad answers.

Request shape:

```json
{"model": "clef", "state": "Player message: ...",
 "questions": {
   "intent": {"type": "choice", "instructions": "...", "criteria": {"explain": "...", "...": "..."}},
   "needs_clarification": {"type": "noul", "instructions": "Is the request too vague to act on?"}}}
```
