## Context

This change comes from a scoping interview on 2026-08-12/14 with the project's target user (a
serious H2H 9-cat grinder, drafting in October 2026), combined with a competitive scan of the
draft-tool market and the academic literature on category-league player valuation. The full trace
is in `discussion.md`; the mathematical assumptions being inherited and attacked are in
`assumptions.md`.

The foundation is unchanged: the point-in-time store, the as-of read layer (no lookahead),
simulate-first league state, and the measured per-player production distributions built for
`matchup-projection`. Those are exactly the substrate `H₀` needs and does not itself have.

## Goals / Non-Goals

**Goals:**
- Value a draft pick by its effect on **category win probability given the roster already built**,
  not by a position on a static list.
- Be **variance-aware** in the specific way weekly H2H demands, using *measured* per-player variance
  rather than the literature's uniform placeholder.
- Produce a **verifiable track record** for drafting, on real data, using the same replay discipline
  as the waiver engine.
- Stay **parameterised by league settings** so format genericity is a configuration change, not a
  rewrite.

**Non-Goals:** roto, auction, keeper, points leagues, web UI, LLM calls. See `proposal.md`.

## Decisions

**D1. The unit of value is category win probability, not a scalar.**
H2H 9-cat is not one optimization; it is nine weekly Bernoulli contests against one opponent. A
scalar aggregate (z-score, fantasy points) throws away the structure that decides matchups. The
engine optimizes one of two objectives, selectable by league setting:

- *Each-category* (categories won accumulate): maximize `Σ_c P(win c)`.
- *Most-categories* (win the matchup): maximize `P(win ≥ ⌈C/2⌉ of C)`, accounting for ties.

For 9 categories the most-categories objective enumerates 2^8 = 256 winning scenarios per
evaluation — ~2,048 multiplications per candidate player, which is tractable.

**D2. One `X-score` basis; G-score and H₀ are two consumers of it.**
Both metrics standardise a player's category contribution into comparable units. The difference is
the denominator and what conditions it. Implementing the basis once yields:

- **G-score** = the static reduction — denominator `√(σ²_M + κ·τ²_M)`, where `σ_M` is player-to-player
  spread of category means and `τ_M` is period-to-period (weekly) spread. Percentage categories use
  the volume-weighted impact form `(μ_A(q)/μ_A)·(μ_R(q) − μ_R)/√(σ²_R + κ·τ²_R)`, consistent with how
  `valuation.py` already handles percentages.
- **H₀** = the dynamic optimizer over that basis (D3).

This matters operationally: G-score is a denominator change to existing code, so it lands early and
**is the September insurance policy** if H₀ slips. It is not a separate product decision.

**D3. H₀ decomposes the category differential into five groups and optimizes over strategy weights.**
At pick `K+1` of `N` rounds, the differential in each category is modeled as a normal whose mean and
variance aggregate: (a) the deciding team's `K` drafted players, (b) the candidate `p`, (c) the
deciding team's `N−K−1` unknown future picks, (d) the opponent's `K+1` known players, (e) the
opponent's `N−K−1` unknown future picks. Variance carries both week-to-week noise and the
player-to-player spread of the *unknown* future picks — the term that makes early picks behave
differently from late ones.

Per candidate, the engine solves a positional assignment (D4), then runs gradient descent (Adam)
over strategy parameters `j` (per-category weights, flex shares), warm-started from the previous
round's solution. The candidate/parameter pair with the highest objective wins.

**Punting is not a feature here.** It is what the optimizer does when concentrating on a subset of
categories maximizes the objective. Removing the punt checkbox is a *product* consequence of D1.

**D4. Positional eligibility is an assignment problem, solved exactly.**
Roster slots are filled via Jonker-Volgenant: already-drafted players score 0 in eligible slots and
−∞ in ineligible ones; prospective future picks score by category alignment (`μ_C · j_C`) plus a
flex bonus. This yields the expected positional composition of future picks, which feeds back into
the differential in D3. Heuristic slot-filling was rejected — it silently mis-prices multi-eligible
players, who are precisely the ones worth paying up for.

**D5. Replace the literature's uniform variance with measured per-player variance.**
`H₀` assumes every player shares a standard deviation (`mτ` counting, `rτ` percentage) and that
distributions are known exactly and static across the season. That is the paper's **first stated
limitation**, and it is the one this repo is best positioned to fix: `store.player_distribution`
already exposes measured per-player per-game σ, validated on the real 2025-26 backfill (see the
`matchup-projection` A1/A2/A4 work, which *deleted* a hand-set variance multiplier once measured σ
proved sufficient). Uniform variance is retained only as a labeled baseline for ablation.

**D6. Category independence is an assumption to test, not inherit.**
`H₀` treats categories as independent. This project has already measured category correlations
(commit `5ccbc0c`, which reframed wire availability as a bundle problem). Correlated categories
change the most-categories objective materially — the 256-scenario enumeration assumes independence
to factorize. v1 ships the independent form because it is what the published result validates;
`assumptions.md` A-DRAFT-3 carries the measurement and the correlated variant as a follow-up.

**D7. Expected games played is first-class, not a haircut.**
The waiver replay found that **31% of adds never played a game** in the period, and that nothing in
the system models games played (`remaining_games_for_team` counts *team* games). Draft value has the
same exposure: a per-game rate is worthless in a category league if the player is not on the floor.
Projections therefore emit expected games played as a distinct output, and the engine consumes
`E[games] × per-game` with the variance that implies. This is not a differentiator — incumbents
project games played too — it is table stakes this system currently lacks.

**D8. Projections sit behind a `ProjectionSource` interface; the shipped implementation is our own.**
The interface exists because the licensing landscape forces it: FantasyPros' API is competent and
cheap but **personal, non-commercial use only**; ESPN's fantasy endpoints are undocumented and
unlicensed; DARKO is the best free rate source but its terms are unstated. Own-built is the only
option with no licensing exposure, and it is what ships. The interface keeps a licensed source
usable for private evaluation and keeps the engine testable against a fixed fixture.

**The dominant term is minutes, not rates.** Age curves are the easy part. The real work is
projecting minutes and role from depth charts and offseason transactions — data the store does not
currently hold at all (see `historical-data-pipeline` deltas).

**D9. Rookies get an explicit, separate path.**
A store of NBA box scores cannot project a player with no NBA games. This is a structural gap, not a
modeling choice. v1 fits a **draft-position prior** from historical rookie seasons (expected
minutes/role/production by draft slot and landing spot), with a manual override table for the ~30
draft-relevant names. Both are labeled provisional and measured in `assumptions.md`; neither is
allowed to masquerade as a model output.

**D10. Opponents are ADP-driven bots, and ADP comes from Yahoo.**
Other drafters pick from an ADP distribution with noise. This matches the published simulations
(making our numbers directly comparable), requires no per-league setup, and generalizes. ADP is
available from Yahoo's `draft_analysis` endpoint — free with the OAuth token the live draft sync
already needs, so no third-party ADP dependency and no licensing question. Modeling *specific*
leaguemates' tendencies is deliberately excluded: highest personal value, zero generality.

**D11. Validation runs on 2025-26 actuals and therefore does not wait on projections.**
This is the sequencing unlock. Draft replay feeds the engine *known* season outcomes as its
projection input, so the optimizer can be built and proven **before** `player-projections` exists.
Consequences:

- The engine and the replay harness are built together; the harness is the engine's test suite.
- It reproduces the published result on real data rather than simulated data, which is a stronger
  claim than the papers make.
- Projections — the long pole, and the only item on the critical path for *both* the September and
  October deadlines — proceed in parallel without blocking the differentiator.

Grading uses realized category wins over the replayed season. Per the standing caution in the
project's replay work, simulated opponents are frozen at draft and drift; draft replay is less
exposed to this than waiver replay (a draft *is* the frozen roster), but standings-based claims stay
caveated and the primary metric is head-to-head strategy-vs-strategy win rate, which is
opponent-symmetric.

**D12. Format genericity is parameterization, deferred but not designed away.**
The engine takes category set, roster slots, team count, rounds, and objective as configuration. Roto
needs a *different objective function* (the ordering space is computationally infeasible to enumerate;
the literature uses heuristics) and auction needs a budget-allocation layer. Neither ships in v1, but
the objective is a seam, not a hardcode.

## Risks / Trade-offs

- **Scope vs. calendar.** This change bundles a novel optimizer, a projection system, live sync, and
  a validation harness against a September public date and an October personal one. The prior GTM
  decision explicitly favored the cheap z-score ranker to protect that window. The mitigation is D11
  (validation unblocks the engine) and D2 (G-score is a working fallback that falls out for free).
  **If the calendar bites, the cut line is `player-projections` sophistication, not the engine** —
  ship a crude minutes model and label it, rather than a z-score product.
- **Gradient descent finds local optima.** The objective is non-convex; the paper acknowledges this.
  Warm-starting from the previous round plus multi-start on early picks bounds the damage. The
  replay harness detects it if it matters.
- **Own-built projections may simply be worse than incumbents'** in year one. That is survivable
  because the edge is in the optimizer, not the means — but it must be *measured*, not assumed, and
  a bad minutes model can swamp a good optimizer. A-DRAFT-5 covers this.
- **Yahoo live polling is unproven in this codebase.** `draft_results` is documented to return picks
  made so far mid-draft, but the fetch layer has never run against a live token, and draft-day is a
  bad time to discover a parsing bug. Manual entry is a required fallback, not a nice-to-have.
