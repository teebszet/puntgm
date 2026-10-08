## Context

Stacked on `change/draft-watch-page` (unmerged): that branch ships the live surface and the
watch page this change redesigns. The basis this change fixes is the basis h-score-draft-engine
built; that change is still open, so every requirement here is ADDED under a new capability and
archive order resolves the overlap.

Evidence base (store as of 2026-10-08, queries recorded in the channel thread): Yahoo ADP
Tatum 8.9 / Haliburton 14.7 / Kyrie 52.0 / Lillard 67.6 against board rank 61 / absent /
absent / absent; DNP zero-rows 2,273 / 2,159 / 1,985 per season diluting per-game means
(Giannis 2025-26: 13.9 pts/game with rows, 18.6 without); `rosterable_pool` ranking on
career-average `usage_role.minutes`.

## Goals / Non-Goals

- Goal: the person on the clock can see the market, their roster's category standing, and
  what punting would do — without leaving the rec card.
- Goal: the four named players are priced by news we enter, visibly, before the real draft.
- Non-goal: an on-clock optimizer. H₀ measured behind the static board 48/48 (3.14/3.15/3.16);
  the differential view is display, and the `recommend()` engine hook stays for when one is
  cleared.
- Non-goal: new data sources, auto-pick, or changes to the published site.

## Decisions

### D1 — Split "how good when he plays" from "does he play", everywhere

DNP zero-rows leave the rate basis and enter the availability model's history. This is the
board's own A-DRAFT-14 separation applied to the basis it computes on; today the rows do both
jobs, which double-counts absence. Rates shrink toward the pool on a thinner sample — that is
the correct direction for a board that projects availability separately.

### D2 — Overrides outrank models, and say so

`player_overrides` is effective-dated (`known_from`), hand-entered, and carries
expected-games / role / note. An override forces pool entry and replaces the projection row;
every applied override is named in the basis line and on the player row (`override:` mark).
A stale or malformed override prints and refuses silently-dropped ingestion — the 2.3a lesson
(a silent parse zero-cost 580 roster rows) applied to hand-entered data. Haliburton /
Lillard / Tatum / Kyrie ship as overrides, not model edits.

### D3 — The projected basis ships labeled-crude; measured stays the default

`--basis projected` wires `DerivedProjectionSource` (the minutes/role model, team-change
reactive) into `build_board`. The Phase-2 cut line ("ship a crude, clearly-labeled minutes
model") licenses this; the 2.11 backtest stays open and its caveat prints with every
projected-basis render. Which basis is default on draft night is Tim's call (tasks gate).

### D4 — Market context is a column, not a valuation input

Yahoo ADP renders on the candidate row (value, `adp`, deviation vs board rank, explicit
absence when unpriced). ADP does not enter the G-score. Deviation is what makes the
injured-four divergence visible at a glance — the market expects them 20-60 picks earlier
than the (broken) board does.

### D5 — The differential is field-relative, recomputed per pick, never an objective

Our roster's projected weekly category profile vs the field's average roster and the field's
strongest roster, from current draft state. This is the honest version of "which cats do I
need to win": a display the manager reads, not weights the optimizer consumes. Reuses the
3.1 differential model's inputs without its objective.

### D6 — Punt scenarios are per-pick computations under a budget, cached by pick

Each punt build re-ranks the remaining pool conditioned on drafted players and the roster
already drafted. All named builds + one custom set compute within `budget_s`; beyond it the
panel shows the last complete set with its pick number, marked stale (the 4.5 degrade
pattern). Builds are cached per pick and invalidated on the next pick.

### D7 — The page stays static; the state contract grows additively

Same `index.html` + `/state.json` + watcher. The dashboard is CSS/JS on the existing page
(no framework, no build step). New state fields (adp, neg_cats, differential, builds) are
additive; the renderer degrades to the fields it knows. Column picker persists in
localStorage; `--columns` on the CLI mirrors the same order.

## Risks / Trade-offs

- Overrides are hand-entered and will be wrong sometimes — visible by design (`override:` +
  note on every affected row) and editable mid-draft.
- Excluding DNP rows changes every board number slightly; the published site boards are
  measured-board artifacts and stay frozen until the next manual rebuild.
- The differential view risks being read as a ranking signal; the spec pins it display-only
  and the renderer labels it "context".
- Custom punt sets multiply compute; D6's budget keeps worst-case latency bounded.

## Migration Plan

Basis fixes change board output on the same inputs — acceptable: no published number depends
on the live-surface board (site boards are frozen exports). Overrides are opt-in rows. The
page redesign keeps the `/state.json` contract; the watcher and page deploy together and the
old page still renders new state.

## Open Questions

- Default basis for draft night: measured (recommended for the first real draft) or
  projected? Tim's call; one flag.
- Which custom punt presets (if any) beyond the named builds?
