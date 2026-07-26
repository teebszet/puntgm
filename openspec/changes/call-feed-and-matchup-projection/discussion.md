# Discussion — call-feed-and-matchup-projection

Feedback trace for Obsidian-mediated review. This change originates from an extended
interview; round 0 records the interview synthesis the specs were derived from.

---

## 2026-07-25 — round 0 (interview synthesis)

**Origin:** the user asked to be interviewed extensively to pull out how a real H2H 9-cat
grinder works the waiver wire, so the log/engine shape is derived from their process rather
than from what's convenient to compute. Structural decision (user): keep the previous change
as the done foundation, archive it, and build this reframe as a **new change**.

**The reframe:** the ranked single-list recommender is **dead**. The product is a
**relevance-weighted call feed** — live signals as games play out, plus an end-of-day
reconciliation into candidate roster moves — leaving the final call (team-build strategy,
risk/reward) to the manager. Design decisions D1–D9 encode the interview; highlights:

- **Waiver-add taxonomy:** schedule streamer (└ one-cat specialist) and ROS value
  (└ injury-return). Streamers secure matchups late; ROS bets win the season early and
  post-trade-deadline and need daily usage data + event-driven usage projection.
- **Usage from first principles:** role = depth chart + injuries + rotation (stabilizing over
  the year). A usage spike is interrogated — who did they leapfrog; does a returning player
  demote them; real youngster breakout vs unsustainable shooting heater. Sources today:
  Yahoo + nba.com minutes and shot attempts.
- **Streamer-spot economy:** maximize games that reach the starting lineup per waiver move,
  bounded by the league's adds-per-week limit and by how many streamer spots can be freed —
  a dynamic, scoreable judgment (sometimes 1 spot and undroppable holds; sometimes open a spot
  for two weeks by dropping a bad-schedule ROS hold you can re-add later).
- **Projection is the spine (D1–D4):** per-category end-of-period distribution = tally +
  Σ(games left × expected/g) with a per-player confidence band; win probability →
  safe/contested/gone. Points/rebounds low-variance, steals/blocks/assists high-variance; you
  often can't call a category until the final 2–3 game-days; injuries re-open cats; a cat is
  only winnable if a mover exists on the wire (backup PGs for assists).
- **Signals & relevance (D5–D6):** strength = confidence × impact-on-contested-cat × relevance;
  soft↔strong. Relevance depends on owner class, live matchup cats, and season stage; opponent
  adds/drops reveal targeted/conceded cats and can trigger pre-emption.
- **Two modes, one feed (D7):** live play-by-play signals + end-of-day reconciliation into
  lines of play with projected deltas (the Mobley→protect-blocks / drop-Landale-add-Nembhard→
  contest-assists example).
- **Scoreable calls (D8):** replay is fully observed, so a suggested move is graded by realized
  category impact vs standing pat — a real counterfactual — plus projection calibration. This
  preserves the "track record is the content" distribution thesis with a richer record than a
  ranked list.

**Spec impact:** new capabilities `matchup-projection` + `call-feed`; `historical-data-pipeline`
gains usage/role + production distributions + variance profiles; `recommendation-log` becomes the
scoreable call-feed log; `decision-engine` (ranker) removed. See proposal.md / design.md.

**Open for review:** the D-level decisions, the safe/contested/gone model choice (normal approx
first), and whether opponent pre-emption is in-scope now or a later refinement.

---

## 2026-07-25 — round 1 (validate assumptions before baking in)

**Raised:** "I'd like to review the maths in the assumptions — e.g. my claim about which cats are
higher variance should be validated before it's built in. In general, anything I say that can be
validated with real data should be validated first."

**Resolved:** Adopted as a standing principle (design D10). Wrote `assumptions.md` — an inventory
of every empirically-checkable assumption (A1 category-variance grouping, A2 variance multiplier,
A3 normal-approx weakness for low-count cats, A4 game independence, A5 trailing-window estimator,
A6 fantasy-value weights, A7 safe/contested/gone thresholds, A8 percentage cats summed = KNOWN-
WRONG, A9 replacement availability, A10 signal-strength bar, A11 season-stage) with the exact
statistic + data to validate each. Key finding: the projector already measures per-player per-game
variance, so the hand-set variance *multiplier* (A2) likely double-counts A1 — validating A1 may
mean deleting the multiplier, not tuning it. Real validation needs the nba_api backfill (synthetic
data is generated from these assumptions, so it can't validate them).

**Spec impact:** `matchup-projection` "Variance-aware projection" softened from asserting the
pts/reb-low, stl/blk/ast-high grouping to requiring an **empirically-measured** profile (defaults
labeled provisional); design D10 added.

**Open for review:** the ledger itself (which assumptions to validate first), and whether to build
the validation/calibration harness now (ready to run on real data) or after you've reviewed the
ledger. Code is unchanged and stays provisional; percentage-category fix (A8) is queued regardless.

---

## 2026-07-26 — round 2 (validated on real data; multiplier removed)

**Context:** NBA's Akamai WAF blocks datacenter/VPN IPs (silent tarpit), so stats.nba.com is
unreachable from the agent's sandbox egress — but fine from the user's residential IP. Implemented
the real nba_api `LeagueGameLog` backfill (`parse_league_game_log`, unit-tested); the user ran it
locally and backfilled the full real 2025-26 season (26,651 player-game lines). The resulting
SQLite is on local disk, so the agent measured directly from it (filesystem is local; only network
is blocked).

**Findings (real data):** CV ranking blk 1.78 > stl 1.27 > fg3m 1.09 > tov 1.04 > ast 0.88 >
pts 0.67 > reb 0.66; lag-1 autocorrelation ≈ 0 across all counting cats (max ~0.09, pts).

**Resolved:**
- **A1 validated** — blk/stl highest, pts/reb lowest (expert intuition confirmed). Correction:
  **assists are not high-variance** (CV 0.88, below median); the asserted stl/blk/ast grouping
  over-included ast, and fg3m is more volatile than ast.
- **A2/A4** — since games are ~independent, Σ rg·σ² is already the correct spread; a category
  multiplier double-counts the measured per-player σ. **Removed the hand-set variance
  multiplier / grouping entirely**; the projector now uses measured σ only. Added
  `measure_autocorrelation` (A4) to the harness; `measure_category_cv` is validation/reporting,
  not a projector input.
- **A8** already fixed (percentage cats volume-weighted).

**Spec impact:** `matchup-projection` "Variance-aware projection" rewritten (measured σ, no
multiplier; validated independence); `config` variance constants deleted; `Projector` variance-
profile plumbing removed. assumptions.md "RESOLVED" section added. 46 tests, ruff clean.

**Verification:** projection on the real season reads sensibly — e.g. a mid-January matchup labels
close low-σ pts as *contested* and a small high-σ stl gap as *contested*, purely from measured σ.

---

## 2026-07-26 — round 3 (A3 + A12 variance-model calibration)

**Raised:** validate the two remaining variance-model assumptions on the real season — A3 (normal
approximation for counting cats) and A12 (binomial SE for percentage cats).

**Method:** added `bootstrap_pct_winprob` (A12) beside `bootstrap_category_winprob` (A3), both
windowed to the projector's last-10-game window for a fair test. Compared the projector's *assumed*
win prob (normal for counting, binomial for pct) to the *empirical* bootstrap across 24 real
matchups (n_boot=400). (First pass had a confound — bootstrap used full-season games vs the
projector's 10-game window; fixed by adding a `window` param.)

**Findings (mean |Δ| assumed-vs-empirical win prob):**
- Counting (normal): pts .18, ast .15, blk .14, tov .12, stl .12, fg3m .10, reb .08 → **avg .124**;
  mild over-confidence bias on **blk (+.04)** (the low-count cat, as theory predicts).
- Percentage (binomial): fg_pct .058, ft_pct .054 → **avg .056**, negligible bias.

**Resolved:**
- **A12** — the binomial percentage model is well-calibrated (~2× better than the counting normal
  approx); streakiness is empirically negligible for win-prob. No change.
- **A3** — the normal approx is *usable but rough* (~0.12 win-prob error, blk over-confident). Kept
  as the fast deterministic default (D9); the windowed bootstrap is the more-accurate alternative and
  is now in the harness. Open question below.

**Spec impact:** none (both are validation/harness additions). `bootstrap_pct_winprob` +
`window` param added; assumptions ledger updated (A3 checked, A12 resolved).

**Open for review:** whether to add an optional bootstrap-backed win-prob projection mode for cases
where label trustworthiness matters (the published track record), accepting the speed cost — or keep
the fast normal approx and treat the ~0.12 gap as acceptable for now.
