# Discussion — bootstrap-historical-data-and-engine

Feedback trace for Obsidian-mediated review. Each round below records what was
raised (in the vault, on mobile), how it was resolved, and which spec sections
changed. Newest rounds at the bottom.

Review artifacts in this change:
- `proposal.md` — why / what / non-goals
- `design.md` — key decisions
- `specs/decision-engine/spec.md`
- `specs/historical-data-pipeline/spec.md`
- `specs/recommendation-log/spec.md`
- `tasks.md` — implementation checklist

---

## 2026-07-24 — round 0 (workflow initialised)

**Raised:** —
**Resolved:** Obsidian review workflow initialised. `openspec/` mirrors to the
`projects` vault under `fantasy-nba-gm/` on every edit; all change files verified
IN_SYNC (git = vault = manifest). Ready for review on mobile.
**Spec impact:** no change — setup only.

---

## 2026-07-24 — round 1 (opponent-relative league state)

Three comments on `design.md`.

**Raised (C1, Non-Goals):** "given my point about whose team the recommendations
are for, do we reconsider [no Yahoo OAuth]? can we get access to each yahoo
league's league state for each point in time?"
**Resolved:** Pushed back on the literal ask — Yahoo exposes only leagues you
belong to, as they exist *now*; there is **no historical point-in-time API** for
arbitrary leagues, so per-date league state cannot be backfilled from Yahoo.
Kept the live-sync non-goal, but the need for league state is real (see C3), so
we now model league state as a first-class point-in-time entity sourced by
**simulation (primary)** plus **read-only import of the user's own past leagues
(secondary)**. Decision: both sources, simulate-first; target up to 3 seasons
(2023-24 → 2025-26) for the real-league validation set where retrievable.
**Spec impact:** design D7/D8 added; Non-Goals reframed; data-pipeline +
rec-log + engine specs extended; tasks §5 added (glue/non-code renumbered to
§6/§7). See files-touched.

**Raised (C2, D4):** "would we try to enrich the official injury reports with
high-confidence injury reports via other media outlets?"
**Resolved:** Great edge for the *live* product (beating the official report is
the streaming edge), but out of scope + anti-lookahead-risky for the backfill:
media signals need accurate historical timestamps, harder to source than
official dated reports. Deferred to live product; cheap forward-compat now —
injury records carry `source` + `confidence` so enrichment slots in without a
migration.
**Spec impact:** design D4 amended; data-pipeline availability requirement gains
source/confidence; recorded as future work, not built.

**Raised (C3, Open Questions — scoring window):** "H2H weekly timeframe wins…
state of the league has to include the H2H matchup opponent… record matchups for
each week for each league, and the replay harness will draw recommendations from
the perspective of who is looking at the waiver wire."
**Resolved:** Agreed — this is the load-bearing insight. Resolved the open
question to **H2H weekly (Mon–Sun) default**. League state now includes weekly
matchup pairing + per-category running tally; recommendations are
**perspective-scoped** (league, team, week, opponent) and the rec log records it.
Scope decision (user): **schema + perspective-aware log now**; skeleton engine
stays a deterministic baseline that *accepts* opponent/category context but does
not yet optimize on it (full opponent-relative optimization = next engine).
New wrinkle from user: league lineup **format changed weekly→daily ~2 seasons
ago**, so lineup/streaming *cadence* is a per-league parameter (weekly-lock vs
daily-change) and pre-change seasons are a materially different game the harness
must treat separately.
**Spec impact:** engine + data-pipeline + rec-log specs extended; design D5
amended, D9 (cadence) added; open questions updated.

**Sync note:** your inline comments lived in the vault copy of `design.md`
(BOTH_DIVERGED). They are fully captured above and addressed in the rewritten
design, so the vault copy was force-synced to the resolved git version — the
comment text is superseded by this round, not lost.
