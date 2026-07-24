"""Matchup projection (D1–D4) — the spine of the product.

For each scoring category, project the end-of-period outcome for both teams as a
distribution: current point-in-time tally + Σ over rostered players of (remaining
scheduled games × expected-per-game) with a variance band. Compare the two
distributions to get a per-category win probability and a safe/contested/gone label.

Variance-aware (D2): high-variance categories (stl/blk/ast) get a wider band, so an
equal margin reads less safe than in a low-variance category (pts/reb). Availability-
reactive (D3): an OUT player contributes nothing to remaining games. Reads only through
the as-of layer, so the projection uses only what was known on the morning of ``as_of``
(the remaining schedule itself is a priori knowledge).
"""

from __future__ import annotations

import math

from fantasy_gm.config import (
    CATEGORY_DIRECTION,
    CATEGORY_VARIANCE_LEVEL,
    GONE_PROB,
    SAFE_PROB,
    VARIANCE_MULTIPLIER,
    Config,
)
from fantasy_gm.models import CategoryProjection, MatchupProjection


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _label(win_prob: float) -> str:
    if win_prob >= SAFE_PROB:
        return "safe"
    if win_prob <= GONE_PROB:
        return "gone"
    return "contested"


class Projector:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()

    def project(
        self, store, league_id: str, team_id: str, as_of: str
    ) -> MatchupProjection:
        meta = store.league_meta(league_id)
        if meta is None:
            raise KeyError(f"unknown league {league_id!r}")
        categories = self.config.categories
        state = store.league_state_asof(league_id, as_of)
        matchup = store.matchup_for_team(league_id, team_id, as_of)
        if matchup is None:
            return MatchupProjection(as_of, league_id, team_id, "", -1, {})
        opponent = matchup.team_b if matchup.team_a == team_id else matchup.team_a
        period_end = matchup.period_end

        mine = self.team_projection(
            store, state.category_tally.get(team_id, {}), state.rosters.get(team_id, []),
            as_of, period_end, categories)
        opp = self.team_projection(
            store, state.category_tally.get(opponent, {}), state.rosters.get(opponent, []),
            as_of, period_end, categories)

        return self._assemble(as_of, league_id, team_id, opponent, matchup.period_index,
                              mine, opp, categories)

    def _assemble(self, as_of, league_id, team_id, opponent, period_index, mine, opp,
                  categories) -> MatchupProjection:
        cats: dict[str, CategoryProjection] = {}
        for c in categories:
            mt, ms = mine[c]
            ot, os = opp[c]
            direction = CATEGORY_DIRECTION[c]
            combined = math.hypot(ms, os)
            diff = direction * (mt - ot)
            if combined == 0.0:
                win_prob = 1.0 if diff > 0 else (0.5 if diff == 0 else 0.0)
            else:
                win_prob = _phi(diff / combined)
            cats[c] = CategoryProjection(c, round(mt, 2), round(ot, 2), round(ms, 2),
                                         round(os, 2), round(win_prob, 4), _label(win_prob))
        return MatchupProjection(as_of, league_id, team_id, opponent, period_index, cats)

    def win_probs_for_roster(
        self, store, league_id: str, team_id: str, as_of: str, roster_ids: list[str]
    ) -> dict[str, float]:
        """Per-category win prob if the deciding team fielded ``roster_ids`` — used to score
        a candidate add/drop swap (opponent held fixed)."""
        state = store.league_state_asof(league_id, as_of)
        matchup = store.matchup_for_team(league_id, team_id, as_of)
        if matchup is None:
            return {}
        opponent = matchup.team_b if matchup.team_a == team_id else matchup.team_a
        categories = self.config.categories
        mine = self.team_projection(store, state.category_tally.get(team_id, {}),
                                    roster_ids, as_of, matchup.period_end, categories)
        opp = self.team_projection(store, state.category_tally.get(opponent, {}),
                                   state.rosters.get(opponent, []), as_of,
                                   matchup.period_end, categories)
        proj = self._assemble(as_of, league_id, team_id, opponent, matchup.period_index,
                              mine, opp, categories)
        return {c: p.win_prob for c, p in proj.categories.items()}

    def team_projection(
        self, store, tally: dict, roster_ids: list[str], as_of, period_end, categories
    ) -> dict[str, tuple[float, float]]:
        """Return {cat: (projected_total, projected_std)} for a roster over the period.
        ``tally`` is the already-accrued point-in-time total; only remaining games change
        with the roster, which is what makes a candidate add/drop swap re-projectable."""
        totals = {c: tally.get(c, 0.0) for c in categories}
        variances = {c: 0.0 for c in categories}
        window = self.config.recent_games_window
        for pid in roster_ids:
            avail = store.availability_asof(pid, as_of)
            if avail and avail.status == "OUT":
                continue  # D3: OUT contributes nothing to remaining games
            scale = 0.5 if (avail and avail.status == "QUESTIONABLE") else 1.0
            nba_team = store.player_team(pid, as_of)
            if nba_team is None:
                continue
            rg = store.remaining_games_for_team(nba_team, as_of, period_end)
            if rg == 0:
                continue
            dist = store.player_distribution(pid, as_of, categories, window=window)
            for c in categories:
                mu, sd = dist[c]
                totals[c] += rg * mu * scale
                variances[c] += rg * (sd ** 2)
        out: dict[str, tuple[float, float]] = {}
        for c in categories:
            mult = VARIANCE_MULTIPLIER[CATEGORY_VARIANCE_LEVEL[c]]
            out[c] = (totals[c], math.sqrt(variances[c]) * mult)
        return out
