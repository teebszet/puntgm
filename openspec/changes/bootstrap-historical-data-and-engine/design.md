## Context

Greenfield project. The build order is deliberately inverted from a normal product: because distribution depends on a published track record, the decision engine and the data it reasons over must exist and be scoreable *before* any UI or Yahoo league sync. The single hardest correctness property in this milestone is **no lookahead bias** — a replay that accidentally sees the future will flatter the engine and the published track record will not survive contact with the live season. Every design decision below serves that property.

## Goals / Non-Goals

**Goals:**
- Backfill the full 2025-26 NBA season into a local, queryable store.
- Guarantee point-in-time correctness: any query can be constrained to "known as of date D."
- Provide a minimal, explainable decision engine for streaming/waiver ranking over a scoring window.
- Log every recommendation with enough context to (a) reproduce it and (b) later score it.
- Keep everything runnable locally by one person with no paid services.

**Non-Goals:**
- No replay harness / eval scoring yet (next change — this milestone only produces the data + engine + log it needs).
- No *live* league sync or daily OAuth-driven updates. A one-time, **read-only** import of the user's *own* past Yahoo leagues (as a validation set, see D7) is in scope as a secondary source; the live product loop is not.
- No LLM calls yet — the skeleton engine uses deterministic heuristics so the harness has a baseline to beat and so behavior is testable.
- No *opponent-relative optimization* in the engine yet — the skeleton *accepts* matchup/category context as input but does not yet optimize on it (that is the headline of the next engine). See D5/D8.
- No media-sourced injury enrichment (D4) — deferred to the live product.
- No UI, no MCP server, no daily-brief job.
- No projections model — recent per-game production stands in for now.

## Decisions

**D1. Data source: `nba_api` for backfill.** Free, covers box scores, schedules, and player game logs from NBA.com. Alternative: balldontlie paid tier — deferred; not worth the cost for a one-time historical backfill. Risk that NBA.com rate-limits/blocks cloud IPs is acceptable because backfill runs locally and once.

**D2. Storage: SQLite (single file) for v1.** Zero-setup, portable, transactional, trivial to snapshot into git-ignored data dir. Alternative: parquet + DuckDB — better for analytics scale but premature; revisit if query performance bites during replay.

**D3. Point-in-time model via effective-dating.** Every mutable fact (especially injury/availability, and any restated stat) is stored with a `known_from` date rather than overwritten. Reads take an `as_of` date and filter `known_from <= as_of`. Immutable facts (a completed game's box score) are dated by game date. This is the core anti-lookahead mechanism. Alternative: snapshot the whole DB per day — simpler conceptually but storage-heavy and awkward to query; effective-dating is the standard pattern.

**D4. Injury/availability is the lookahead danger zone.** Box scores are naturally point-in-time (a game that hasn't happened has no stats). Injuries are not: it is tempting to backfill "player X missed Jan 10–24" as a block. Instead capture dated injury-report states so the engine only sees the designation as it was reported that morning. Where perfectly-dated historical injury data is unavailable, record the limitation explicitly in the data provenance rather than silently importing hindsight. Each availability record carries a `source` and `confidence` so that future media-sourced enrichment (beat-writer reporting, the live-product streaming edge) can be layered in without a schema migration — but that enrichment is a Non-Goal here because it demands accurately-timestamped historical reports we can't reliably source for a backfill.

**D5. Engine is deterministic and explainable — and perspective-aware in shape.** Candidate score = f(games in window, recent per-game production, availability). Every score carries a human-readable reasoning string. This makes the engine (a) unit-testable, (b) a legitimate baseline for the future LLM engine to beat, and (c) already content-ready ("here's why it picked X"). The engine *signature* accepts the full league state — including the week's matchup opponent and per-category standing (D8) — so opponent-relative optimization can be added by the next engine without changing callers; the skeleton itself may ignore or only lightly weight that context.

**D6. Recommendation log is append-only and structured.** One row per recommendation: `created_at`, `as_of_date`, `league_state_ref`, `perspective` (league / team / week / opponent — see D8), `candidate`, `rank`, `score`, `reasoning`, `confidence`. Append-only so the track record can never be quietly rewritten — credibility of the eventual public claims depends on this.

**D7. League state is a first-class point-in-time entity, sourced simulate-first.** H2H waiver decisions are opponent- and category-relative, so the store must hold, effective-dated: rosters, the weekly matchup schedule (who plays whom each week), and per-category running tallies. Yahoo has **no historical point-in-time API for arbitrary leagues** (it exposes only leagues you belong to, as they exist now), so this cannot be backfilled from Yahoo directly. Two sources, in priority order: **(a) simulated leagues** — draft from ADP, snake-draft rosters, generate a weekly matchup schedule — reproducible and scalable to many league shapes; **(b) read-only import of the user's own past Yahoo leagues** as a real-world validation set, targeting up to three seasons (2023-24 → 2025-26) where retrievable (final standings, draft results, and weekly matchup results survive per-season; roster-as-of-date history is partial and its provenance is recorded per D4's pattern).

**D8. Recommendations are perspective-scoped.** A recommendation is always made from the point of view of one (league, team, week, opponent). The engine takes that perspective as input and the log records it, so the future harness can score "given manager M's roster and this week's opponent, was this the right pickup?" rather than an opponent-agnostic ranking. `league_state_ref` + `perspective` together pin down exactly what the engine saw.

**D9. Lineup cadence is a per-league parameter, and format changed mid-window.** Leagues differ in how often lineups/rosters can change: weekly-lock (set once, Mon–Sun) vs daily-change (the streaming game). The user's own league moved weekly→daily ~2 seasons ago, so across the 3-season validation window the *game itself* differs — seasons before the change are not directly comparable to after. Cadence is therefore a first-class league setting (`lineup_cadence`), the scoring window defaults to weekly Mon–Sun but is parameterized, and the harness (next change) must segment results by cadence rather than pooling them.

## Risks / Trade-offs

- Lookahead bias creeping in via injury data → Mitigation: effective-dating (D3/D4), plus a test that asserts an `as_of` query never returns rows with `known_from > as_of`.
- NBA.com endpoint instability / rate limits → Mitigation: backfill locally, cache raw responses to disk, make the pipeline resumable/idempotent.
- Over-engineering the engine before the harness exists → Mitigation: keep it deterministic and small; the skeleton accepts perspective/matchup context (D5/D8) but need not optimize on it — the next change (harness) will reveal what actually matters.
- Simulated leagues unrepresentative of real play (draft quality, waiver behavior) → Mitigation: keep the simulator behind a clean interface, seed drafts from real ADP, and validate against the user's own imported leagues (D7) before trusting harness numbers.
- Yahoo historical import thinner than hoped (roster-as-of-date largely unavailable; format change limits comparability) → Mitigation: treat real-league import as a *validation* set, not the primary substrate; record provenance gaps (D4 pattern); segment by `lineup_cadence` (D9).
- SQLite outgrown by replay scale → Mitigation: DuckDB is a drop-in later; keep data access behind a thin repository layer.

## Migration Plan

Greenfield — no migration. Deployment is "clone + install + run backfill script." Rollback is deleting the local data dir.

## Open Questions

- Best available source for *dated* historical injury reports (vs. retrospective injury blocks)? May require scraping archived reports; scope in the next change if it blocks scoring quality. (Media-sourced enrichment — D4 — is a related future item, gated on the same timestamping problem.)
- ~~Exact scoring-window definition (H2H weekly vs. rolling 7 days)~~ **Resolved (round 1):** default to **weekly (Mon–Sun)**, parameterized; `lineup_cadence` (weekly-lock vs daily-change) is a per-league setting (D9).
- Simulated-league fidelity: which ADP source and draft model produce leagues realistic enough for the harness to trust? (D7)
- Which past seasons are actually retrievable from Yahoo for the user's own leagues, and how much roster-as-of-date history survives per season? Confirm before committing to the 3-season validation target. (D7)
