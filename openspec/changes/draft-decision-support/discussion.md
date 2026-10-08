# Discussion — draft-decision-support

## 2026-10-08 — round 0 (origin: Tim's draft-night feedback, channel events cf25252 + be1ff88)

**Raised:** Six points from the 2026-10-08 mock rehearsal: usage projections for 2026/27;
rec-card columns (Yahoo ADP, top negative cats, column options); whether recs update on the
cats needed against the field (the G-score question); injured players (Haliburton, Lillard,
Tatum, Kyrie) ranked wrong; punt scenarios on the fly; page restyled as a dashboard.

**Resolved:** Mapped to requirements below; the three measured defects (injured stars
absent/mis-ranked, DNP-diluted rates, career-average pool ranking) carry store evidence from
2026-10-08 queries. G-score answer recorded honestly in the spec: it is league-wide value;
the team-relative optimizer measured worse 48/48 and stays off — Tim gets the differential
view and punt scenarios instead.

**Spec impact:** all of it — this is the origin round.

### Requirement → delivery map

| Requirement | Kind | Delivered by | Read at |
|---|---|---|---|
| Per-game rates over games actually played | new | task 1.1 | to fill |
| Draft pool reflects current role | new | task 1.2 | to fill |
| News overrides place and price a player | superseded round 1 — replaced by live-data adjustments, see round 1 map | — | — |
| Forward basis available and labeled | new | task 3.1 | to fill |
| Recommendations carry market context + columns | new | task 4.2 | to fill |
| Differential view vs field (display-only) | new | task 5.1 | to fill |
| Punt scenarios explorable on the clock | new | task 6.1 | to fill |
| Watch page is a drafting dashboard | new | task 7.1 | to fill |

## 2026-10-08 — feedback round 1 (origin: Tim's Obsidian comment on design.md D2)

**Raised:** "i don't think we want overrides, as this will go out of date quickly and be hard
to adjust as the season goes on. i prefer adjustments based on projections and baselines and
news" (vault-side edit appended to D2).

**Resolved:** Request for change, accepted — the hand-entered `player_overrides` table is
dropped. Pricing adjusts from data that maintains itself, three ways: (1) **status** — the
league platform's structured per-player status is ingested as effective-dated `Availability`
rows (`source=yahoo`); the board's projected-availability mode (already the default) consumes
them, and a mid-season status change is just a new row, so no hand edits; (2) **baselines** —
a player with no usable ranked-season sample prices per-game rates from his most recent
season above the games-played floor (last healthy baseline, under D1's games-played rule)
and is placed by the derived-depth rule (R2 gains that injury edge); (3) **projections** —
the projected minutes/role basis (D3) carries role onto 2026-27 rosters. Dated player news
renders on rows as display context; news text is never a pricing input. The four named
players flow through the pipeline with no special entry. Verified against the code before
writing: `Availability` (status ACTIVE|QUESTIONABLE|OUT, effective-dated, source+confidence)
and the projected-availability default already exist (`models.py:36`, `draft/board.py:74`,
`cli.py:841`); today only synthetic rows feed it, so the build is extraction + fallback, not
a table.

**Spec impact:** R3 replaced (live-data adjustments, no overrides); R2 amended (a season lost
to injury counts as no usable sample for placement); D2 rewritten; proposal "What Changes" +
Data + Non-goals updated; tasks §2 replaced, §8.1 reworded.

**Vault note:** Tim's comment lived in the vault copy of `design.md`; superseded by this
round — force-synced.

### Requirement → delivery map (round 1 update)

| Requirement | Kind | Delivered by | Read at |
|---|---|---|---|
| Availability adjusts from ingested platform status, not hand rows | new | task 2.1 | to fill |
| Last-healthy-baseline pricing for unusable ranked seasons | new | task 2.2 | to fill |
| Provenance names each source; news display-only; unpriced surfaced | new | task 2.3 | to fill |
| Draft pool reflects current role — injury-wiped season = no usable sample | corrected | task 2.2 (contradicts: ranked-season minutes would zero-rank an injury-wiped season; test pins placement by derived depth) | to fill |
