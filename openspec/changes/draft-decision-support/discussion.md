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
| Per-game rates over games actually played | new | task 1.1 | `fantasy_gm/valuation.py` `_player_games(played_only)` + `fantasy_gm/draft/xscore.py:349` @ 557c77f; suite 356 passed at that sha |
| Draft pool reflects current role | new | task 1.2 | `fantasy_gm/valuation.py` `rosterable_pool` (season window + depth placement) @ 557c77f; tests `test_valuation.py` ranked-season + depth, run at that sha |
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
| Availability adjusts from ingested platform status, not hand rows | new | task 2.1 @ ecafe58 (`status.py`; rate caps live in `rate_factor`) | live ingest 2026-10-08: 500 rows → 87 stored, 0 unmapped (`runs/status-ingest-478-l-25733.json`) |
| Last-healthy-baseline pricing for unusable ranked seasons | new | task 2.2 @ ecafe58 (`valuation.last_healthy_sample`, ≥10-game floor) | tests pin Haliburton baseline + Tatum-unchanged; the four verified in the 2026-27 pool |
| Provenance names each source; news display-only; unpriced surfaced | new | task 2.3 @ ecafe58 (basis line, `st`/note columns, `*` baseline marker, unpriced list) | board renderers + `board_json` export the new fields |
| Draft pool reflects current role — injury-wiped season = no usable sample | corrected | task 2.2 (contradicts: ranked-season minutes would zero-rank an injury-wiped season; test pins placement by derived depth) | both halves delivered: placement task 1.2 @ 557c77f, rates task 2.2 @ ecafe58 |

## 2026-10-08 — build note: basis fixes landed; evidence re-measured

Tasks 1.1–1.3 built and committed at `557c77f` (suite 356 passed at that sha, pushed).
On the live store: the depth rule places **6** previously-absent players into the pool —
Kyrie Irving, Damian Lillard, Bradley Beal, Tyrese Haliburton, Fred VanVleet, Walker
Kessler — at 156 pool size (six lowest minutes-ranked players fall off the bottom).
They have placement but no rate rows yet; task 2.2 prices them.

**Evidence re-measure (corrects the round-0 context numbers).** Re-running the DNP census
against the current store does not reproduce the round-0 figures. Measured 2026-10-08:
all-zero stat rows per season are **800 / 730 / 638** (2023-24 / 2024-25 / 2025-26), of which
only **118 / 100 / 83** have zero recorded minutes (true DNP rows); the rest are real
garbage-time games (2–5 minutes, a turnover, no shots) and correctly stay in the rates.
Giannis Antetokounmpo has **36** 2025-26 rows, none DNP, mean 27.6 pts — the round-0
"13.9 with rows / 18.6 without" example does not reproduce on this store. The round-0
counts (2,273 / 2,159 / 1,985 per season; Giannis example) are unlocated — likely a
different census (possibly zero-weeks from idle-week filling, not DNP rows). The fix is
spec-correct regardless (a DNP row is a game not played, by definition); the mechanism is
what changed, not the magnitudes. Design.md context figures stand corrected by this note.

## 2026-10-08 — build note: live-data pricing landed (tasks 2.1–2.4)

Committed at `ecafe58` (suite 381 passed, ruff clean at that sha). What shipped, and what
the real league says:

- **Status ingest (2.1).** `fantasy_gm/projections/status.py` parses Yahoo's
  `players;out=metadata` — `status_full`, dated `injury_note`,
  `player_notes_last_timestamp` — into effective-dated `availability` rows keyed
  (player, known_from, source), idempotent. The board consumes them as availability-rate
  caps: OUT-for-season ×0.0, short-term OUT ×0.5, QUESTIONABLE ×0.75; an ACTIVE row never
  raises a rate.
- **Baseline pricing (2.2).** A player whose ranked seasons hold no usable sample prices
  per-game rates from his most recent healthy season (≥10-game floor): Haliburton, Lillard
  and Kyrie price from 2024-25. Tatum keeps his own (usable) sample, unchanged. The
  board's `as_of` turned out to guard the availability fit only — feeding it into rate
  measurement zeroed everyone; removed and pinned by a test.
- **The four on live data (2.4, `runs/status-ingest-478-l-25733.json`).** 500 rows → 87
  designations (71 QUESTIONABLE, 15 OUT, 1 ACTIVE), 0 unresolved names. **None of the four
  carries a designation** — Haliburton/Lillard/Kyrie price via baseline + depth placement,
  Tatum via his own sample; all four verified in the 2026-27 pool. Real rows the board now
  sees: LeBron (Q, Rest), Luka (Q), Curry (Q), Mark Williams (OUT, shoulder), Shaedon
  Sharpe (OUT, knee).
- **Status map finalized.** Live data surfaced two values the round-1 map did not carry:
  52× 'Not Active' (roster state — not on an NBA roster — now ignored and reported as
  `ignored`, never priced; availability does not own roster reasons) and 1× 'Probable'
  (→ ACTIVE; the mildest NBA designation, and ACTIVE never raises a rate). Re-ingest
  clean: 0 unmapped, 0 unresolved.
- **Consume-side gap fixed.** No production surface passed `forward_season`, so the pool
  the four need never materialized; `FORWARD_SEASON=2026-27` now wired through `build_gm`
  (draft sessions + watcher), `cmd_board --forward-season` and `build_site.py`.
