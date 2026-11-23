# Vision

What this app should become and how I'll use it. Written 2026-11-23 from a design conversation.
Issues on the fork track the roadmap below.

## Who it's for

Me. One PoE2 player, one Mac, run locally in Docker with my MindRouter key. No accounts, no
hosting, no other users. That rules out a lot of work (auth, cost control, GGG app registration)
and lets the app fit how I play instead of a general audience.

## How I'll use it

While I play, the same few questions keep coming up:

- Where is my build struggling right now?
- What should my next passive point be?
- Which notable or keystone should I path towards?
- I just got a new skill or opened a support slot. Which gems are good, and why?
- Is this item better than what I'm wearing?

I think about all of these as marginal gains. Each answer is "this change adds X damage and
costs Y defence". The app measures those with PoB's calculation engine and shows them. It does
not decide for me.

Each time I level up or find something, I export the PoB code from poe.ninja, paste it into the
app and look at the dashboard.

## Decisions

**Comparison tool, not advisor.** The app shows damage and defence changes side by side, and I
choose. It never folds them into one score.

**The change preview is the core element.** It works like Diablo's gear comparison. Every
candidate change (passive, path, gem, item) gets a card listing stats that go up in one colour
and down in another. The card always shows a fixed core set, plus any other stat that changed:

| Group | Stats |
|---|---|
| Damage | Total DPS of the main skill |
| Pools | Life, Energy Shield, Spirit |
| Mitigation | Armour, Evasion, fire/cold/lightning/chaos resistance on one row |
| Survival | Effective HP, max hit by damage type on one row |

Anything else that changes (movement speed, attack speed, crit, regen) is listed under the core
set.

**Dashboard first, chat second.** Pasting a code fills a dashboard with the answers to my usual
questions without me asking. Chat sits beside it for follow-ups ("why does that node help?",
"what changes against a cold boss?").

**Numbers come from the engine, never the model.** Charts and cards render engine output
directly. The LLM picks candidates to test and explains results. In chat it keeps saying plainly
when something can't be checked by the engine, such as boss mechanics or prices, instead of
stating guesses as fact.

**Import by pasting a code.** That's enough for now. The official GGG API would be better, but
GGG isn't accepting new applications (their developer docs, checked 2026-11-23). poe.ninja's
character endpoints are internal and not for third-party use, so the app won't scrape them. A
browser bookmarklet that sends the code from a poe.ninja page I'm already on would remove the
copy and paste without breaking either site's rules. Low priority.

## The views

I taught data visualization, so these get designed together, not handed over finished. Tools
are d3 for custom views (the tree, the interactive scatter) and Observable Plot for the standard
charts, inside the existing React app.

1. **Weak spots.** Max hit by damage type as bars with threshold lines for content I care
   about. Uncapped resistances flagged.
2. **Next node.** A scatter of every reachable node, damage change against defence change.
   Nodes that no other node beats on both axes are highlighted, since those are the real
   choices. Point cost shows as size or colour. Hover shows the change preview.
3. **Support gems.** Small multiples, one row per candidate gem for an empty slot: DPS gain,
   Spirit or reservation cost, anything it costs defensively. A waterfall can split one gem's
   gain into the multipliers it adds.
4. **Pathing.** The real passive tree drawn from PoB's node coordinates, nodes coloured by
   marginal value for a stat I pick. Paths to distant notables drawn with their value per point
   spent, so cheap filler nodes count towards the notable at the end.
5. **Progress.** DPS, EHP and resistances against character level, one point per pasted code.
   Shows where the build fell behind.

### How we design each view

For every view the app exposes the real data from my build as a JSON endpoint and a
downloadable file. I can sketch in an Observable notebook against my own numbers while Claude
sketches in the app. We compare, then build the agreed version. Nobody designs from fake data.

## Roadmap

| # | Piece | Needs |
|---|---|---|
| 0 | Keep every pasted build as a snapshot with a timestamp and level | Storage only. Do this first, because history that wasn't saved can't be recovered |
| 1 | Change preview card and dashboard layout | Every view uses the card |
| 2 | Weak spots view | Data exists today |
| 3 | Next-node scatter | `rank_passives` exists; needs both damage and defence deltas per node |
| 4 | Support gem comparison | New engine command that tries unowned gems in an empty socket |
| 5 | Tree heatmap and pathing | Tree coordinates from PoB, plus a way to value multi-node paths |
| 6 | Progress view | Snapshots from step 0 |

## Out of scope for now

- Other users, hosting, accounts.
- Automatic sync with my character.
- An optimizer that searches whole trees or gear sets. Lemhi could run one later if the views
  show it's worth it.
- CLEF intent routing (see IDEAS.md). The dashboard answers the common questions, so routing
  chat messages matters less than it did.
