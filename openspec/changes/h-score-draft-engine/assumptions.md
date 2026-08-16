# Assumptions ledger — draft engine

Every mathematical assumption inherited from the `H₀` literature or introduced here, whether it is
**inherited** (stated by the paper, adopted as-is), **asserted** (our intuition, unmeasured), or
**known-wrong** (mathematically incorrect, fix regardless) — plus the exact statistic that would
validate it and the data needed.

**Principle (project standing rule): nothing asserted here stays a hard-coded constant once real
data exists.** Each becomes a measured parameter or is replaced. This ledger exists because the GTM
is a verifiable track record — asserted-but-unmeasured parameters undermine the entire pitch.

The published `H₀` result is validated against *simulated* seasons sampled from actual performances.
This project has the real 2025-26 backfill, so several of these are checkable here in ways the
papers did not check them.

Status legend: **INHERITED** · **ASSERTED** · **KNOWN-WRONG** · **HEURISTIC**

---

## A-DRAFT-1. Uniform per-player variance — KNOWN-WRONG (paper's own #1 limitation)

**Claim:** every player's counting stats share a standard deviation `mτ`, and percentage stats share
`rτ`. Used to make the G-score denominator tractable.

**Why wrong:** production consistency varies enormously between players. A high-usage guard's
week-to-week assist spread is nothing like a bench big's. Treating them as equal mis-prices exactly
the consistency the H2H format rewards.

**Fix (D5):** use measured per-player per-game σ from `store.player_distribution`, already validated
on the real backfill for `matchup-projection`. Retain uniform-τ as a labeled ablation baseline.

**Validate:** replay with measured-σ vs uniform-σ and compare strategy win rates. If measured σ does
*not* improve on uniform, that is a publishable finding in itself and the simpler form should win.
**Data:** real per-player game logs (have).

---

## A-DRAFT-2. Player distributions are known exactly and static across the season — INHERITED

**Claim:** a player's true production distribution is fixed and known at draft time.

**Reality:** roles change (trades, injuries, breakouts). This is *the* reason the in-season Co-GM
exists. At draft time it is a more defensible simplification than mid-season, but it systematically
understates the value of players with wide role uncertainty (rookies, post-trade situations) and
overstates the reliability of any point projection.

**Fix:** projections carry an uncertainty band on the mean, not just on game-to-game variance — this
repo already made exactly this correction for `matchup-projection` (commit `f628629`, "account for
estimated-mean uncertainty"). Apply the same treatment here.

**Validate:** measure realized-vs-projected dispersion by preseason role certainty bucket.
**Data:** needs a completed season projected in advance; 2025-26 backtest is the proxy.

**Status (Track B, 2.8): implemented, not yet validated.** `CategoryEstimate` carries
`mean_stderr` separately from `per_game_std`, and the derived source propagates both through
`rate × minutes` by the delta method, so a player's band widens for a short history, a role
change, or a team change independently of how volatile they are game to game. The *dispersion*
check still needs a season projected in advance, so it inherits A-DRAFT-5's blocker.

---

## A-DRAFT-3. Categories are independent — INHERITED, measurably false

**Claim:** category outcomes are independent, which lets the most-categories objective factorize
into 2^(C−1) enumerable scenarios.

**Reality:** already measured false in this repo (commit `5ccbc0c`, category correlations reframing
wire availability as a bundle problem). Points/FG%/FGA move together; steals and blocks do not.

**Impact:** correlation changes `P(win ≥5 of 9)` even when every marginal `P(win c)` is unchanged.
Positively correlated categories make extreme outcomes (8-1, 1-8) more likely and coin-flip
matchups less likely — which is precisely the regime punting exploits.

**v1 decision:** ship the independent form (it is what the published result validates), then measure.
**Validate:** compare independent-factorized `P(win ≥5)` against a Monte-Carlo draw from the measured
category covariance, using the existing `bootstrap_category_winprob` machinery from A3 of the
projection work. **Data:** real box scores (have) + existing correlation measurement (have).

---

## A-DRAFT-4. `κ` — the period-to-period variance weight — HEURISTIC

**Claim:** a single constant relates player-to-player and period-to-period variance in the G-score
denominator.

**Reality:** `κ` should depend on games per scoring period, roster size, and category. A 4-game week
and a 2-game week are not the same problem, and the whole point of the metric is that period
structure matters.

**Validate:** derive `κ` per category from the real backfill (weekly aggregation of game logs), and
compare against the paper's value. Check sensitivity: if strategy win rate is flat in `κ` over a
plausible range, stop tuning and say so. **Data:** real game logs + a weekly period calendar (have).

---

## A-DRAFT-5. Own-built projections are good enough — ASSERTED, highest-risk item

**Claim:** a minutes/role projection built from this store is accurate enough that the optimizer's
edge survives.

**Why it matters:** a superior optimizer fed bad means loses to a z-score tool fed good means. This
is the assumption most likely to sink the product, and it is entirely unmeasured today.

**Validate:** backtest 2026-27 projections' *method* on 2025-26 — project that season from
2024-25-and-prior inputs and score MAE on minutes and on each category, against (a) last-season
naive, (b) a published projection set as a reference point. **Gate: if own-built cannot beat naive
last-season carry-forward on minutes MAE, it is not ready and the honest move is to say so.**
**Data:** requires 2024-25 backfill in addition to 2025-26 — *not currently held*.

**Status (Track B, 2.11): STILL OPEN — the gate has not been passed, and the model must not be
described as validated.** The harness is built (`projections/backtest.py`, `fantasy-gm
projection-backtest`) and runs in two modes. The cross-season mode — the actual gate — is
**blocked on the 2024-25 backfill (task 2.10)**, which needs a network that can reach
`stats.nba.com`; it reports the blocker rather than degrading to a number that looks like a
result.

What *can* be measured today is the split-season proxy: fit through 2026-01-15, score on
2026-01-16 → 2026-04-12, 290 players.

| | minutes MAE | bias | games MAE | categories beaten |
|---|---|---|---|---|
| model | **2.97** | −0.36 | **5.38** | 7 of 9 |
| naive carry-forward | 3.04 | −0.49 | 5.55 | — |

That is +2.3% on minutes, but the **paired** difference is 0.7σ and the model is closer on only
53% of players — i.e. inside the noise. The harness reports this as `INCONCLUSIVE`, not `PASS`,
and the CLI exits non-zero. Two reasons not to read the proxy as the gate: both sides see the
same season's team context, and the role model has no forward depth chart to react to, so the
single mechanism the model has that carry-forward does not is inert. It understates the model's
advantage and is not a substitute for 2.10.

---

## A-DRAFT-6. Rookie draft-position prior — ASSERTED

**Claim:** expected rookie minutes/production can be predicted from draft slot and landing spot.

**Reality:** rookie outcomes are famously high-variance and the sample per slot is small. This is a
prior, not a model, and its variance should be wide enough to reflect that.

**Validate:** fit on historical rookie seasons, report out-of-sample error by slot bucket, and make
the resulting uncertainty band explicit in the projection. If the band is as wide as the signal, say
so and let the optimizer price the uncertainty rather than hiding it.
**Data:** multi-season rookie histories — *not currently held*.

**Status (Track B, 2.9): the asserted surface is now one number per slot bucket, and it is
labeled.** `projections/rookies.py` expresses the prior as draft slot → expected *rotation rank*,
then runs that rank through the **measured** minutes curve and the **measured** per-minute rate
tiers. So the only asserted link is `FALLBACK_SLOT_RANK` (1-5 → rank 6, 6-14 → 8, 15-30 → 10,
31-60 → 12, undrafted → 13); everything downstream of it is fit from real games. `fit_rookie_prior`
replaces those numbers with the measured median rank of a past cohort as soon as one is in the
store, and every projection carries `prior_basis` = `fitted` or `fallback` so the two can never be
confused. The band is the measured spread of minutes *within* that rank plus the team-change drift
term, which makes rookie `mean_stderr` materially wider than an established player's — as intended.
**Still open:** the out-of-sample error by slot bucket, which needs the multi-season history from
task 2.10.

---

## A-DRAFT-7. Expected games played is separable from per-game production — ASSERTED

**Claim:** value factorizes as `E[games] × E[per-game]`.

**Reality:** these correlate. Players returning from injury are often minutes-limited; load
management concentrates on high-usage veterans. The factorization overstates the value of a
high-rate, low-availability player.

**Validate:** measure the correlation between games played and per-game production within player-season,
and between availability and minutes on return. **Data:** real game logs + injury designations (have).

**Status (Track B, 2.7): MEASURED — the assumption is false, and by enough to matter.** From the
real 2025-26 backfill (`measure_games_production_correlation`, 506 players):

* `corr(games played, minutes/game)` = **+0.479**
* `corr(games played, points/game)` = **+0.336**
* minutes in the first three games back from an absence of ≥8 days = **0.907×** the player's own
  season average (n=1,318 returns)

So availability and production are positively correlated across players — the durable players
*are* the high-minute players — while within a player, returning from an absence costs ~9% of
their minutes. The two effects push season value in opposite directions and the factorization
`E[games] × E[per-game]` captures neither.

**v1 decision:** expected games played ships as a separate output with its own band (which is the
part that was missing entirely), the factorization is retained, and the covariance term is
**reported rather than modeled** — adding it would change the `ProjectionSource` contract that
Track A is already coding against. Carried forward as the follow-up: value the covariance term and
decide whether it is worth a contract change.

---

## A-DRAFT-8. ADP-driven bots represent real drafters — ASSERTED

**Claim:** modeling opponents as ADP + noise is a faithful enough draft-room simulation.

**Reality:** real drafters exhibit positional runs, homer picks, and correlated punt strategies that
independent ADP sampling will not produce. The paper's simulations assume even less (near-random),
so this is an improvement on the published baseline, but it is not reality.

**Consequence if wrong:** scarcity is mis-estimated, so the engine's willingness to wait on a
position is mis-calibrated — the error shows up as reaching or as being sniped.

**Validate:** compare bot-generated draft boards against real completed 2025-26 draft boards
(available via the Yahoo import work) — distribution of pick-vs-ADP deviation, run lengths by position.
**Data:** real drafts from imported leagues — *dependency on the parallel Yahoo branch*.

---

## A-DRAFT-9. Gradient descent reaches a good-enough optimum — HEURISTIC

**Claim:** warm-started Adam on a non-convex objective lands close enough to optimal.

**Validate:** on a subset of picks, brute-force or multi-start heavily and measure the objective gap
against the warm-started single run. If the gap is material on early picks (where the strategy space
is widest), raise multi-start count there only. **Data:** none external; a compute experiment.

---

## A-DRAFT-10. Depth-chart position means rotation rank — ASSERTED (structural)

**Claim:** the `depth_chart_pos` the projection model consumes can be read as *rotation rank within
the team* (1 = the team's biggest-minutes player).

**Why it is asserted:** the store holds no player positions — not in `player_logs`, not anywhere —
so "third-string centre" is not expressible and "seventh in the rotation" is. Every fit in
`projections/minutes.py` is against rank derived from mean minutes within team, which is the only
reading the data supports.

**Consequence if wrong:** a positionally-scarce player (a starting centre on a team with a deep
guard rotation) is ranked by minutes rather than by the scarcity that actually earns them minutes,
so their projection is too low. This bites hardest exactly where the D4 positional-assignment work
says multi-eligible players are mispriced.

**Validate:** refit the minutes curve on (position, depth-at-position) and compare minutes MAE
against the rank-only curve.

**Status: the data dependency is resolved; the measurement is not.** NBA's `playerindex` endpoint
returns a listed position for every player in one batched call, and `data/player_index.py` now
ingests it into `player_positions` (also feeding `forward_roster` and `incoming_players` — see
A-DRAFT-12). So positions are available for D4's slot assignment, and the refit above is now
runnable rather than blocked. Two caveats to state when it is run: `playerindex` is a *current*
snapshot, so applying it to past seasons assumes a player's listed position is near-static, and
NBA's listed position ("G-F") is a coarser thing than the fantasy platform's eligibility, which is
what D4 actually needs and which comes from Yahoo with the task 4.1 OAuth.

---

## A-DRAFT-11. Minutes-model parameters — MEASURED (2025-26 backfill)

Recorded here because the standing rule is that nothing asserted stays a constant. Every parameter
below is fit by `fit_minutes` / `fit_rates` / `fit_games` from games known as of the projection
date, and each carries a `basis` field that reads `measured` or `fallback` so a projection built on
an unidentifiable fit is never mistaken for one built on data. As of `2026-08-17`, on the real
2025-26 backfill (506 players), all of them read `measured`:

| parameter | value | what it does |
|---|---|---|
| recency half-life | **10 games** | how fast a player's own minutes history decays; chosen by held-out error *inside* the training window, over a grid from flat to 6 games |
| within-player minutes σ | 6.78 min | game-to-game noise |
| between-player minutes σ | 8.24 min | player-to-player spread of true mean minutes |
| shrinkage weight | 0.68 games | prior weight toward the pool mean (small: minutes are well-identified) |
| period-to-period drift σ | 4.32 min | how far a player's *true* minutes move between halves, net of sampling noise |
| team-change drift multiplier | **×1.45** (55 movers) | how much more uncertain a moved player's role is — mover/stayer drift ratio |
| role curve | rank 1 → 33.7 min … rank 12 → 16.0 min | expected minutes by rotation rank; the entry point for a stated depth chart and for the rookie prior |
| availability prior | 2.6 games, pool rate 0.645 | beta-binomial shrinkage on games played |

**Note on the recency half-life:** 10 games is short, which says role is substantially
non-stationary within a season — the reason a forward projection needs a depth chart at all.

**Note on the availability prior:** at 2.6 games of prior weight, expected games played is close to
a carry-forward of the player's own rate, and the backtest bears that out (games MAE 5.38 vs 5.55).
The availability model is the weakest component and the one with the most headroom.

---

## A-DRAFT-12. Minutes ordering carries across a team change — ASSERTED

**Claim:** a projected depth chart can be derived by ranking a team's incoming roster on each
player's own minutes history, without an external depth chart. A player who out-earned his new
teammates elsewhere will out-earn them here too.

**Why it is needed:** `forward_roster.depth_chart_pos` is the input that makes the projection react
to an offseason move, and it had no source — the table was empty, so the role mechanism was inert
on real data and the model degraded to carry-forward. `playerindex` supplies the *team* but no
depth. The alternatives were to leave depth unknown (the model does nothing new), hand-enter ~450
ranks, or pay to source depth charts. Deriving rank from the new roster's own track records needs
none of those, and it is the more primitive claim.

**Where it is wrong:** it is blind to fit and to contract. A high-minutes player joining a team
that already has a star at his position will not simply displace him — position is exactly the
thing rank cannot see (A-DRAFT-10). It is also blind to the reason a player moved: a veteran
signing for a smaller role ranks by his old minutes, not his new job. Both errors are
*systematic*, not noise: they over-project the incoming player and under-project the incumbent.

**Mitigation in place:** a team change already inflates the drift term (measured ×1.45), so a moved
player's band widens even where the mean is wrong. The derived rank is a labeled default, not a
verdict — `role` records `returning` or `no-history`, and a hand-entered `forward_roster` row with a
later `known_from` supersedes it, which is the intended workflow for the names worth disagreeing
about.

**Validate:** with two seasons backfilled (task 2.10), derive the depth chart for the later season
from the earlier one and score minutes MAE against (a) the flat carry-forward baseline and (b) the
realized rotation rank. If derived rank does not beat carry-forward, the mechanism is decoration
and should be replaced by manual entry for the draft-relevant pool only.
**Data:** two backfilled seasons — *blocked on 2.10*.
