"""Matchup projection (D1–D4) — the spine of the product.

For each scoring category, project the end-of-period outcome for both teams as a
distribution: current point-in-time tally + Σ over rostered players of (remaining
scheduled games × expected-per-game) with a variance band. Compare the two
distributions to get a per-category win probability and a safe/contested/gone label.

Variance-aware (D2): each category's uncertainty band comes from the **measured** per-player
per-game σ (Σ rg·σ²), so an equal margin reads less safe in a genuinely more volatile category
(blk/stl) than in a stable one (pts/reb) — no hand-set category multiplier. Real 2025-26 data
showed game-to-game production is ~independent (lag-1 autocorrelation ≈ 0), so Σ rg·σ² is the
correct spread and a multiplier would double-count the σ already in the model (see assumptions
ledger A1–A2/A4). Availability-reactive (D3): an OUT player contributes nothing to remaining
games. Reads only through the as-of layer (the remaining schedule is a priori knowledge).
"""

from __future__ import annotations

import math

from fantasy_gm.config import (
    CATEGORY_DIRECTION,
    GONE_PROB,
    PERCENTAGE_CATEGORIES,
    SAFE_PROB,
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

        ps = matchup.period_start
        mine = self.team_projection(
            store, state.category_tally.get(team_id, {}), state.rosters.get(team_id, []),
            as_of, ps, period_end, categories)
        opp = self.team_projection(
            store, state.category_tally.get(opponent, {}), state.rosters.get(opponent, []),
            as_of, ps, period_end, categories)

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
        ps = matchup.period_start
        mine = self.team_projection(store, state.category_tally.get(team_id, {}),
                                    roster_ids, as_of, ps, matchup.period_end, categories)
        opp = self.team_projection(store, state.category_tally.get(opponent, {}),
                                   state.rosters.get(opponent, []), as_of, ps,
                                   matchup.period_end, categories)
        proj = self._assemble(as_of, league_id, team_id, opponent, matchup.period_index,
                              mine, opp, categories)
        return {c: p.win_prob for c, p in proj.categories.items()}

    def team_projection(
        self, store, tally: dict, roster_ids: list[str], as_of, period_start, period_end,
        categories
    ) -> dict[str, tuple[float, float]]:
        """Return {cat: (projected_total, projected_std)} for a roster over the period.
        Counting cats: banked tally + Σ(remaining games × expected/g), variance Σ rg·σ².
        Percentage cats (A8): project makes and attempts separately, then the ratio, with a
        binomial standard error — never a sum of per-game percentages. Only remaining games
        change with the roster, which is what makes an add/drop swap re-projectable."""
        counting = [c for c in categories if c not in PERCENTAGE_CATEGORIES]
        pct = [c for c in categories if c in PERCENTAGE_CATEGORIES]
        comp_keys = sorted({k for c in pct for k in PERCENTAGE_CATEGORIES[c]})
        dist_keys = counting + comp_keys

        totals = {c: tally.get(c, 0.0) for c in counting}
        variances = {c: 0.0 for c in counting}
        # banked (already-played) makes/attempts over [period_start, as_of]
        proj_comp = dict(store.category_totals(roster_ids, period_start, as_of, comp_keys)) \
            if comp_keys else {}
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
            dist = store.player_distribution(pid, as_of, dist_keys, window=window)
            for c in counting:
                mu, sd = dist[c]
                totals[c] += rg * mu * scale
                variances[c] += rg * (sd ** 2)
            for k in comp_keys:
                proj_comp[k] = proj_comp.get(k, 0.0) + rg * dist[k][0] * scale

        out: dict[str, tuple[float, float]] = {}
        for c in counting:
            # measured per-player σ only; games are ~independent (A4) so no multiplier
            out[c] = (totals[c], math.sqrt(variances[c]))
        for c in pct:
            mk, at = PERCENTAGE_CATEGORIES[c]
            makes, attempts = proj_comp.get(mk, 0.0), proj_comp.get(at, 0.0)
            p = makes / attempts if attempts > 0 else 0.0
            std = math.sqrt(p * (1 - p) / attempts) if attempts > 0 else 1.0
            out[c] = (p, std)
        return out
