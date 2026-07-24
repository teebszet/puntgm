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
