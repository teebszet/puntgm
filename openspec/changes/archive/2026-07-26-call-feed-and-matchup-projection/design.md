## Context

This change is driven by an extended interview with a target user (a serious H2H 9-cat
grinder). The through-line: waiver work is the most engaging part of the season, and the
manager does not want their judgment replaced by a ranked list — they want the *right data,
most current, most actionable*, surfaced and weighted for their situation, so they make the
final call on team-build strategy and risk/reward. The full trace is in `discussion.md`.

Everything here sits on the previous change's foundation: the point-in-time store, the
as-of read layer (no lookahead), and simulate-first league state. Those are unchanged.

## Goals / Non-Goals

**Goals:**
- Project each scoring category's end-of-period outcome as a distribution, for both teams.
- Produce a relevance-weighted, strength-graded feed of signals + an end-of-day
  reconciliation into candidate roster moves with projected category impacts.
- Keep every surfaced call timestamped and *scoreable* so the track record survives.

**Non-Goals:**
- No ranked single-list recommender (explicitly removed).
- No UI / real-time push infrastructure yet — this change defines the engine + log; the
  "live play-by-play" delivery surface is a later change.
- No live Yahoo OAuth sync. Opponent moves are modeled from data we already hold (simulated
  leagues now; imported real leagues later).
- No LLM calls yet — projections and signal detection are deterministic and testable so the
  replay harness has a baseline, and so calibration can be measured.

## Waiver-add taxonomy (from the interview)

Signals and moves are typed against how the manager actually thinks:

- **Schedule streamer** — maximize games that *reach the starting lineup* this week (± next),
  per waiver move spent. The workhorse of late-season matchup-securing.
  - *One-cat specialist* — a streamer subtype that also swings a single target category
    (e.g. a backup PG for assists); must still clear the games-remaining bar first.
- **Rest-of-season (ROS) value** — rare, high-reward; sacrifices a streamer slot for upside.
  Peaks early season (unsettled roles) and post-shake-up (trade deadline). Depends on daily
  usage data + usage-change projection after events.
  - *Injury-return* — an ROS subtype driven by return-timing rumor, with a real cost curve
    (too early wastes a slot; too late it gets sniped).

## Decisions

**D1. The spine is category-outcome projection, not ranking.** For each category, project the
end-of-period result as a distribution: `current_tally + Σ(games_remaining × expected_per_game)`
with a **per-player confidence/variance band** (consistency varies widely). Compare the two
teams' distributions to get a per-category **win probability** and a **safe / contested / gone**
label. Signal relevance, "finely balanced cat," and reconciliation all derive from this.

**D2. Category variance profiles are first-class.** Points and rebounds are low-variance
(leads are safer, comebacks harder); steals, blocks (and often assists) are high-variance
(leads never fully safe, comebacks live). Variance sets how quickly a category can be called,
and honest uncertainty must be shown — you often cannot call safe/contested/gone until the
final 2–3 game-days, once actual game counts and who-plays resolve.

**D3. Projection is availability- and schedule-shock reactive.** Injuries re-open settled
categories and change both sides' remaining game counts. Projections update as availability
and the resolving schedule change; the schedule itself remains a priori knowledge (from the
foundation) while outcomes/availability stay effective-dated.

**D4. Replacement availability modulates "winnable."** A contested category is only worth
chasing if a mover exists on the wire — assists are catchable partly because backup PGs are
always available. Projection feeds "is it winnable"; the feed feeds "can I act on it."

**D5. Signals carry a strength on a soft→strong spectrum.** Display strength =
`confidence × impact-on-a-contested-category × relevance-to-my-build/season-stage`. A signal
tips from soft to strong when it is sustained over N games, has an identifiable **depth-chart
cause** (who did the player leapfrog; does a returning player demote them), and swings a
contested category. Unsustainable shooting heaters and one-week mirages stay soft.

**D6. Relevance is situational and opponent-aware.** The same trend is weighted differently
by *whose* player it is (mine / opponent's / free agent / a trade target I track), by which
categories are live in this matchup, and by season stage — early season boosts usage-breakout
signals, late season boosts pure schedule/matchup-securing signals. Reading an opponent's
adds/drops reveals their targeted vs conceded categories and can trigger pre-emption (grab the
obvious best streamer before they can, which then sets my category plan).

**D7. Two temporal modes, one feed.** *Live mode* streams signals during games (play-by-play).
*End-of-day reconciliation* runs after the day's games and before the day's waiver processing,
summarising the day's relevant signals into candidate roster moves — each a distinct **line of
play** for the rest of the matchup — with projected per-category deltas and the required
add/drop. (Dropping a player who has not yet played that day for one who has is possible but
rare and flagged.)

**D8. Every call is scoreable; that is the track record.** The log records each signal and
each reconciliation move, timestamped. In replay the world is fully observed, so a suggested
move ("drop A, add B on day D") is graded by its **realized** category impact vs. standing pat
— a genuine counterfactual (we have B's actual production for the rest of that week). Secondary
grade: **projection calibration** — did categories called "safe" actually hold. This is a
richer, more defensible record than a ranked list ever was.

**D9. Deterministic baseline first.** Projections (expected-per-game, variance) and signal
detection are deterministic and testable this change, so the harness can measure calibration
and the future LLM engine has a baseline to beat.

**D10. Validate assertions before baking them in.** Any domain claim that can be checked against
real data (e.g. the category-variance grouping in D2) is treated as **provisional** and must be
*measured*, not asserted, before it hardens into a system parameter. Asserted constants are
labeled provisional; the projector should consume a data-derived profile from a validation/
calibration harness rather than hand-set values. Real validation needs the `nba_api` backfill —
the synthetic season is generated from these same assumptions and so cannot validate them. The
full inventory (what to measure, with which statistic and data) is in `assumptions.md`. This is
not incidental: the product's "track record is the content" thesis depends on measured, not
asserted, parameters. Known-wrong maths (percentage categories summed instead of volume-weighted)
is fixed regardless of validation.

## Risks / Trade-offs

- Projection overconfidence early in a period → Mitigation: show distributions/uncertainty,
  widen bands by category variance (D2), never assert safe/gone before evidence supports it.
- Feed noise drowning signal → Mitigation: strength = confidence × impact × relevance (D5/D6);
  default the feed to strong, relevant signals and let soft ones be opt-in.
- Usage/role data latency or gaps → Mitigation: point-in-time with provenance (foundation
  pattern); degrade gracefully to minutes/attempts when depth-chart context is missing.
- Counterfactual grading complexity → Mitigation: replay is fully observed, so move value is
  directly computable from historical box scores; keep the grader separate from the engine.

## Open Questions

- Exact win-probability model per category (normal approximation on per-game mean/variance vs.
  empirical simulation of remaining games)? Start simple (normal approx), revisit with calibration.
- How to source/estimate **usage-change projections after events** (trade/injury) — heuristic
  depth-chart reshuffle vs. learned model? Heuristic first.
- Opponent behavior model fidelity for pre-emption — how far ahead can we credibly predict an
  opponent's next add?
- Thresholds for soft vs strong and for safe/contested/gone — calibrate against replay rather
  than hand-set.
