# Proposal: web-decision-tools

## Why

Tim, 2026-11-23 (fantasy-nba-gm event 14cbe5ae): the daily digest posts read as
overconfident noise — point projections with no spread on them. The decision
surface moves to a web page with uncertainty on every number (per-category σ and
25–75% bands), checked when wanted instead of pushed daily.

## What changes

- Fixture-grid compute: team defense (volume-weighted allowed rates),
  direction-aware favorability, position cells, 14-day grid payload.
- Wire-lines board: the daily waiver threshold table codified from Tim's
  hand-run tables, cell-exact including tie order.
- A Yahoo-gated FastAPI serving board payloads (matchup with σ + bands, teams,
  games remaining) plus a generated static `/tools` page.
- Deploy: vendored `fantasy_gm` package + bundled store snapshot committed
  in-repo; push to `main` = production deploy; daily snapshot refresh via the
  launchd wrapper.

## Non-goals

- No accounts beyond Tim's guid allow-list; no writes to Yahoo; no LLM calls.
- No silent re-basing of the committed board (the committed `site/dist` is the
  board of record).
