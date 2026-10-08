## Why

Draft-night feedback from the 2026-10-08 mock rehearsal (Tim, fantasy-nba-gm event `cf25252`,
plus a follow-up the same day): the tool answers "who is good" but not "who is good for my
team right now", omits or mis-prices exactly the players the market is still drafting, hides
market context it already holds, and gives no way to explore punt builds while the clock runs.
The watch page reads like a blog, not a drafting dashboard.

The rehearsal surfaced three concrete defects, measured on the store as of 2026-10-08:

- **Injured stars are invisible or mis-ranked.** Haliburton (Yahoo ADP 14.7), Lillard (67.6)
  and Kyrie Irving (52.0) have zero 2025-26 logs, so they never enter the top-156-by-minutes
  pool — the live recommendations cannot name them. Tatum played 16 games and sits at rank 61
  against Yahoo ADP 8.9. The pool and the per-game basis have no mechanism for "known to miss
  time" news.
- **The per-game basis is diluted by DNP rows.** Every season's game-log ingest carries
  zero-stat rows for games a player did not play (2,273 / 2,159 / 1,985 rows in
  2023-24 / 2024-25 / 2025-26). `measure_per_game_stats` averages them in: Giannis's 2025-26
  per-game points read 13.9 with the rows included, 18.6 without. The board's own separation
  of availability from rate (A-DRAFT-14) is defeated when the rate also carries the absence.
- **Pool ranking mixes seasons.** `rosterable_pool` ranks by `AVG(minutes)` over every
  `usage_role` row ever ingested (2023 through 2026), so a veteran's pool position reflects a
  career average, not his current role — Tim's point that "all the rosters have changed".

And three capability gaps: recommendations carry no market context (Yahoo ADP is ingested —
187 priced players — but displayed nowhere), nothing is roster-relative (the replay verdict
that H₀ loses 48/48 to the static board means the optimizer stays off, but Tim still needs to
see which categories his roster must win), and punt builds exist only as a pre-draft static
export, not as an on-the-clock exploration.

## What Changes

- **Fix the basis** (`player-projections`) — per-game rates measured over games actually
  played (DNP zero-rows excluded from rates, retained by the availability model); draft pool
  ranked by 2025-26 minutes only.
- **Add news overrides** (`player-projections`) — an effective-dated manual override table per
  player (expected games, role, note) that forces a player into the pool and labels the row.
  This is how Haliburton/Lillard/Tatum/Kyrie get priced: by news the model cannot see, entered
  by us, visibly.
- **Add a forward basis option** (`player-projections`) — `board --basis projected` builds the
  board from the derived minutes/role model (which reacts to team changes), clearly labeled,
  with the 2.11 backtest caveat printed. Default stays measured.
- **Make recommendations carry market context** (`draft-decision-support`) — Yahoo ADP,
  ADP deviation, and top *negative* categories become candidate fields; a column model lets the
  manager choose what the card shows.
- **Add a category differential view** (`draft-decision-support`) — our current roster's
  projected category profile against the field's average and strongest rosters, recomputed
  after every pick. Information, not an optimizer: H₀ stays unshipped per the replay verdict.
- **Add live punt scenarios** (`draft-decision-support`) — re-rank the remaining pool under
  each punt build (named and custom) against the current draft state, with a per-build roster
  outlook, bounded by the on-the-clock budget.
- **Redesign the watch page as a drafting dashboard** (`draft-decision-support`) — dense,
  low-chrome layout: clock header, recommendation card with column picker, differential bars,
  punt panel, roster rail, compact pick log. Same static page + `/state.json` contract, no
  framework.

## Impact

- **Capabilities:** new `draft-decision-support`, carrying both the basis fixes and the
  rec-surface additions as its own requirements (h-score-draft-engine's `player-projections`
  and `draft-surface` deltas are untouched; at archive time the overlap — the basis this
  change fixes is the basis that change built — is resolved by archive order).
- **Depends on** `change/draft-watch-page` (this branch stacks on it — the page it ships is
  the one being redesigned).
- **Data:** one new table (`player_overrides`); no schema changes elsewhere. ADP data already
  ingested.
- **Non-goals:** no resurrection of H₀ as the on-clock optimizer; no new data sources; no
  auto-pick; no change to the published site boards.
