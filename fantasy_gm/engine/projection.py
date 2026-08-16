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
    PERCENTAGE_SHRINKAGE,
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
    def __init__(self, config: Config | None = None, method: str = "normal",
                 n_boot: int = 600, seed: int = 0, participation: bool = True,
                 shrink_percentages: bool = True):
        self.config = config or Config()
        # win-prob method: "normal" (fast Φ, default) or "bootstrap" (Monte-Carlo over real
        # per-game lines — ~2× better calibrated for counting cats, A3, at a speed cost).
        self.method = method
        self.n_boot = n_boot
        self.seed = seed
        # A13: scale remaining *team* games by the player's measured participation rate.
        # Toggleable so the replay harness can A/B it; on by default because assuming every
        # scheduled game is played is wrong by a factor of ~2 (see store.participation_rate).
        self.participation = participation
        # A14: regress trailing shooting rates toward the league rate. Toggleable so the
        # replay harness can A/B it.
        self.shrink_percentages = shrink_percentages

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
        season = meta["season"]
        mine = self.team_projection(
            store, state.category_tally.get(team_id, {}), state.rosters.get(team_id, []),
            as_of, ps, period_end, categories, season)
        opp = self.team_projection(
            store, state.category_tally.get(opponent, {}), state.rosters.get(opponent, []),
            as_of, ps, period_end, categories, season)

        win_probs = None
        if self.method == "bootstrap":
            win_probs = self._bootstrap_winprobs(
                store, state.rosters.get(team_id, []), state.rosters.get(opponent, []),
                as_of, ps, period_end)
        return self._assemble(as_of, league_id, team_id, opponent, matchup.period_index,
                              mine, opp, categories, win_probs)

    def _assemble(self, as_of, league_id, team_id, opponent, period_index, mine, opp,
                  categories, win_probs=None) -> MatchupProjection:
        cats: dict[str, CategoryProjection] = {}
        for c in categories:
            mt, ms = mine[c]
            ot, os = opp[c]
            if win_probs is not None:
                win_prob = win_probs[c]
            else:
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

    def _bootstrap_winprobs(self, store, my_roster, opp_roster, as_of, period_start,
                            period_end) -> dict[str, float]:
        """Per-category win prob by Monte-Carlo: resample each player's recent real per-game
        lines over their remaining games (whole lines, so within-game category structure is
        preserved) and compare team totals. Windowed to the projector's recent-games window."""
        import random

        cats = self.config.categories
        counting = [c for c in cats if c not in PERCENTAGE_CATEGORIES]
        comp_keys = sorted({k for c in cats if c in PERCENTAGE_CATEGORIES
                            for k in PERCENTAGE_CATEGORIES[c]})
        window = self.config.recent_games_window

        def _side(roster):
            bt = store.category_totals(roster, period_start, as_of, counting + comp_keys)
            banked = {k: bt.get(k, 0.0) for k in counting + comp_keys}
            draws = []
            for pid in roster:
                avail = store.availability_asof(pid, as_of)
                if avail and avail.status == "OUT":
                    continue
                nba_team = store.player_team(pid, as_of)
                if not nba_team:
                    continue
                rg = store.remaining_games_for_team(nba_team, as_of, period_end)
                if rg == 0:
                    continue
                lines = [lg.stats for lg in store.player_logs_asof(as_of, player_id=pid)][-window:]
                if lines:
                    draws.append((rg, lines))
            return banked, draws

        my_banked, my_draws = _side(my_roster)
        op_banked, op_draws = _side(opp_roster)
        rng = random.Random(self.seed)
        keys = counting + comp_keys
        wins = {c: 0.0 for c in cats}

        def _totals(banked, draws):
            t = dict(banked)
            for rg, lines in draws:
                for _ in range(rg):
                    g = rng.choice(lines)
                    for k in keys:
                        t[k] += g.get(k, 0.0)
            return t

        for _ in range(self.n_boot):
            my, op = _totals(my_banked, my_draws), _totals(op_banked, op_draws)
            for c in counting:
                d = CATEGORY_DIRECTION[c] * (my[c] - op[c])
                wins[c] += 1.0 if d > 0 else (0.5 if d == 0 else 0.0)
            for c in cats:
                if c in PERCENTAGE_CATEGORIES:
                    mk, at = PERCENTAGE_CATEGORIES[c]
                    mp = my[mk] / my[at] if my[at] > 0 else 0.0
                    op_p = op[mk] / op[at] if op[at] > 0 else 0.0
                    wins[c] += 1.0 if mp > op_p else (0.5 if mp == op_p else 0.0)
        return {c: wins[c] / self.n_boot for c in cats}

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
        meta = store.league_meta(league_id)
        season = meta["season"] if meta else None
        mine = self.team_projection(store, state.category_tally.get(team_id, {}),
                                    roster_ids, as_of, ps, matchup.period_end, categories,
                                    season)
        opp = self.team_projection(store, state.category_tally.get(opponent, {}),
                                   state.rosters.get(opponent, []), as_of, ps,
                                   matchup.period_end, categories, season)
        proj = self._assemble(as_of, league_id, team_id, opponent, matchup.period_index,
                              mine, opp, categories)
        return {c: p.win_prob for c, p in proj.categories.items()}

    def _shrunk_rate(self, store, cat, mean_makes, mean_att, n_obs, season) -> float:
        """Trailing shooting rate regressed toward the league rate (A14).

        ``k`` is in units of attempts, so a high-volume shooter is shrunk proportionally
        less than someone with a handful of tries — which is the whole point.
        """
        makes, att = mean_makes * n_obs, mean_att * n_obs
        if att <= 0:
            return 0.0
        k = PERCENTAGE_SHRINKAGE.get(cat, 0.0)
        if k <= 0 or not self.shrink_percentages:
            return makes / att
        from fantasy_gm.valuation import league_percentage_rates

        league = league_percentage_rates(
            store, season or self.config.primary_season).get(cat, makes / att)
        return (makes + k * league) / (att + k)

    def team_projection(
        self, store, tally: dict, roster_ids: list[str], as_of, period_start, period_end,
        categories, season: str | None = None
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
            # A13: a *scheduled* team game is not a *played* player game. Measured mean
            # participation is 0.49, so q rescales rg into expected games played.
            q = 1.0
            if self.participation:
                measured = store.participation_rate(
                    pid, as_of, window=self.config.participation_window)
                if measured is not None:
                    q = measured
            eg = rg * q  # expected games played over the remaining schedule
            dist, n_obs = store.player_distribution_with_n(pid, as_of, dist_keys, window=window)
            for c in counting:
                mu, sd = dist[c]
                totals[c] += eg * mu * scale
                # Per *scheduled* game production is a mixture: 0 with probability (1−q),
                # otherwise ~(μ, σ). So Var per scheduled game is q·σ² + q(1−q)·μ², and the
                # second term is the DNP risk itself — a 30-point scorer who plays half the
                # time is far less certain than his σ alone implies. Games are independent
                # (A4), so it scales with rg.
                variances[c] += rg * (q * sd ** 2 + q * (1.0 - q) * mu ** 2)
                # … plus uncertainty in the *estimated* mean. We only have an N-game sample,
                # so μ̂ carries standard error σ²/N — and that error shifts every remaining
                # game the same way, so it scales with the square of expected games, not eg.
                # Omitting this was making extreme win probabilities overconfident
                # (97% predicted → 88% realized).
                if n_obs > 1:
                    variances[c] += (eg ** 2) * (sd ** 2) / n_obs
            # Percentage cats (A14): project *attempts* from the trailing mean, but derive
            # projected *makes* from a rate shrunk toward the league rate. An unshrunk
            # trailing rate treats a 12-for-13 stretch as 92% true talent, which inflated
            # percentage-category win probabilities and made the engine chase FT% fights
            # whose projected edge was sampling noise. Already-banked makes/attempts are
            # realized facts and are never shrunk — only the remaining schedule.
            for cat in pct:
                mk, at = PERCENTAGE_CATEGORIES[cat]
                proj_att = eg * dist[at][0] * scale
                if proj_att <= 0:
                    continue
                rate = self._shrunk_rate(store, cat, dist[mk][0], dist[at][0], n_obs, season)
                proj_comp[at] = proj_comp.get(at, 0.0) + proj_att
                proj_comp[mk] = proj_comp.get(mk, 0.0) + rate * proj_att

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
