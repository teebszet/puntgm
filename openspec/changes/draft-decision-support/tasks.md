## 1. Basis fixes (R1, R2) — the numbers under everything

- [x] 1.1 `measure_per_game_stats` excludes DNP rows (no playing time) from rate means and
      pool eligibility; `measure_period_stats`/availability keep them as games not played.
      Pin the split with a test on a fixture containing both row kinds
- [x] 1.2 `rosterable_pool` ranks by the ranked season's minutes only (`usage_role` filtered
      to that season's window); no-history players rank by derived `forward_roster` depth.
      Test: a veteran whose minutes fell between seasons keeps his new-season rank
- [x] 1.3 Basis line gains the rate rule ("rates over games played; DNPs carried by
      availability"); re-export nothing published

## 2. Live-data pricing (R3) — status ingest + last-healthy baseline

- [x] 2.1 Ingest platform per-player status into `availability` (`source=yahoo`,
      effective-dated): extend the saved-payload Yahoo import to extract status (+ dated
      editorial notes for display); the CLI prints per-player coverage. Verify the payload
      carries status at implementation; report loudly if it does not
- [x] 2.2 Last-healthy-baseline pricing: when the ranked season has no usable sample, rates
      come from the most recent season above the games-played floor; placement follows the
      derived-depth rule (R2's injury edge). Test: Haliburton (zero 2025-26 logs) enters
      the pool priced from 2024-25 rates + OUT status; Tatum's rates are unchanged (usable
      sample)
- [x] 2.3 Provenance + display: basis lines and rows name each applied source (status row,
      baseline season, projected model); the dated news headline renders as context; a
      player neither sample nor baseline can price is reported as unpriced with the reason
- [x] 2.4 Coverage check on the four (Haliburton, Lillard, Tatum, Kyrie): after ingest,
      verify status rows + basis lines; record their pricing in runs/

## 3. Forward basis option (R4)

- [x] 3.1 `--basis projected` on `board`/`build_gm`: `DerivedProjectionSource` feeds
      per-game rates; basis line carries projection date + 2.11 caveat. Default unchanged
- [x] 3.2 Test: projected basis reacts to a 2026-27 team change (mover's minutes follow the
      new depth); measured basis bit-identical to today under no flag

## 4. Recommendation columns (R5)

- [x] 4.1 `Candidate` gains `adp`, `adp_dev` (ADP minus board rank), `neg_cats`; explicit
      absence for unpriced players; page state schema grows additively
- [x] 4.2 Column model: `recommend --columns` / watch-page column picker persisted in
      localStorage; default set rk, player, value, vs safe, surv, adp, top cats, neg cats
- [x] 4.3 Terminal renderer and page table render from the same column spec
- [x] 4.4 Test: unpriced player renders explicit absence; picker persists; unknown column
      names are reported not dropped

## 5. Category differential view (R6)

- [ ] 5.1 Per-category profile of our roster vs field-average roster vs field-strongest
      roster, from current draft state, recomputed per pick; reuses the 3.1 differential
      model's inputs without its objective
- [ ] 5.2 Page bars + terminal block, labeled "context"; test: display-only (candidate
      ordering is unchanged by differential computation)

## 6. Live punt scenarios (R7, R8)

- [ ] 6.1 Punt panel: named builds + custom cat set re-rank the remaining pool against
      current state; per-build top candidates + roster outlook
- [ ] 6.2 Budget: all builds within `budget_s`; beyond it the last complete set shows with
      its pick number, marked stale (4.5 pattern); per-pick cache invalidated on next pick
- [ ] 6.3 Test: builds exclude drafted players; custom set accepted; stale marking on
      overrun

## 7. Dashboard page (R9)

- [ ] 7.1 Redesign `index.html`: fixed clock header, rec card with column picker, differential
      bars, punt panel tabs, roster rail, compact pick log; dense dark theme, phone-portrait
      usable; same static page + `/state.json` contract, additive state
- [ ] 7.2 Screenshot pass: desktop + phone widths against the mock rehearsal, posted for Tim

## 8. Rehearsal and gates

- [ ] 8.1 Re-run `scripts/mock_draft_harness.py` with status ingest + columns + punt panel;
      record the four players' pricing before/after the status+baseline pipeline in runs/
- [x] 8.2 **HUMAN GATE — spec direction.** Tim reviews this change (Obsidian) before
      implementation starts. Waits on: Tim — cleared 2026-10-08 ("ok build it", after round 1)
- [ ] 8.3 **HUMAN GATE — default basis for draft night.** Measured (recommended) vs
      projected; one flag, but it is a product call. Waits on: Tim
- [ ] 8.4 **HUMAN GATE — dashboard sign-off.** Tim approves the layout after the screenshot
      pass. Waits on: Tim
- [ ] 8.5 Dry-run the full live path against a real Yahoo mock (h-score-draft-engine 4.8)
      with the new surface once gates 8.2-8.4 clear and the draft date is known
