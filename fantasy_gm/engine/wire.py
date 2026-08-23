"""Wire availability analysis (A9) — bundle scarcity + the marginal trade-off surface.

You never swap categories one-for-one: players come in correlated positional bundles, so the
real question isn't "how deep is the wire in assists" but "which bundle is available, and what do
I concede to get it." Measured on the real 2025-26 season (`measure_category_correlations`), the
two anti-correlated clusters are the **big** bundle (reb·blk·fg_pct) and the **guard** bundle
(ast·stl·fg3m·ft_pct); pts/tov track usage and belong to neither.

This module answers two things for a deciding team:
  1. **Marginal trade-off** — for each contested category, the best available add's full
     gain/concede vector (via re-projection), and a verdict: *chase* (nets out ahead),
     *trade-off* (helps the cat but concedes more elsewhere), or *infeasible* (none improves it).
  2. **Bundle scarcity** — how many available players fall in each bundle, so you can see
     whether the bundle you need is even on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Perspective
from fantasy_gm.valuation import _player_games, player_values, rosterable_pool

# Bundles derived from the measured category-correlation clusters (A9), not asserted.
BUNDLES: dict[str, tuple[str, ...]] = {
    "big": ("reb", "blk", "fg_pct"),
    "guard": ("ast", "stl", "fg3m", "ft_pct"),
}
_DELTA = 0.03  # win-prob move that counts as a real gain/concede


def _rank_direction(cat: str) -> int:
    """Percentage cats are ranked by signed impact, so they sort +1 regardless of the raw
    category direction (see ``Reconciler._cat_recent``)."""
    return 1 if cat in PERCENTAGE_CATEGORIES else CATEGORY_DIRECTION[cat]


@dataclass(frozen=True)
class WireOption:
    category: str          # the contested category considered
    add_id: str
    add_name: str
    gain: float            # win-prob improvement in `category` from the best available add
    concedes: dict[str, float]  # other cats that drop by >= _DELTA (negative win-prob deltas)
    net_categories: int    # #cats improved minus #cats worsened across the whole matchup
    verdict: str           # "chase" | "trade-off" | "infeasible"


@dataclass
class WireAnalysis:
    perspective: Perspective
    options: list[WireOption]      # one per contested category
    bundle_depth: dict[str, int]   # available player count per bundle


class WireAnalyzer:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.projector = Projector(self.config)

    def analyze(self, store, league_id: str, team_id: str, as_of: str,
                shortlist: int = 10) -> WireAnalysis:
        proj = self.projector.project(store, league_id, team_id, as_of)
        if not proj.opponent_id:
            return WireAnalysis(Perspective(league_id, team_id, -1, ""), [], {})
        season = store.league_meta(league_id)["season"]
        matchup = store.matchup_for_team(league_id, team_id, as_of)
        state = store.league_state_asof(league_id, as_of)
        my_roster = list(state.rosters.get(team_id, []))
        rostered = state.rostered_player_ids()
        persp = Perspective(league_id, team_id, proj.period_index, proj.opponent_id)
        base = {c: proj.categories[c].win_prob for c in self.config.categories}

        values = player_values(store, season)
        drop = min(my_roster, key=lambda p: values.get(p, -999.0)) if my_roster else None

        wire = []
        for pid, name, _t in store.player_universe(season, as_of):
            if pid in rostered:
                continue
            nba_team = store.player_team(pid, as_of)
            if nba_team and store.remaining_games_for_team(nba_team, as_of, matchup.period_end) > 0:
                wire.append((pid, name))

        options = [
            self._best_for_cat(store, league_id, team_id, as_of, my_roster, drop, wire, cat,
                               base, shortlist, season)
            for cat in proj.contested()
        ]
        return WireAnalysis(persp, options, self._bundle_depth(store, season, wire))

    def _best_for_cat(self, store, league_id, team_id, as_of, my_roster, drop, wire, cat,
                      base, shortlist, season=None) -> WireOption:
        # shortlist the wire by recent contribution in the target cat (bounds the re-projection)
        scored = sorted(
            ((self._recent(store, pid, as_of, cat, season) * _rank_direction(cat), pid, name)
             for pid, name in wire),
            reverse=True,
        )
        best = None
        for _s, pid, name in scored[:shortlist]:
            new_roster = [p for p in my_roster if p != drop] + [pid]
            after = self.projector.win_probs_for_roster(
                store, league_id, team_id, as_of, new_roster)
            gain = after.get(cat, base[cat]) - base[cat]
            if best is None or gain > best[0]:
                best = (gain, pid, name, after)
        if best is None or best[0] <= 0.01:
            return WireOption(cat, "", "", 0.0, {}, 0, "infeasible")
        gain, pid, name, after = best
        deltas = {c: after.get(c, base[c]) - base[c] for c in self.config.categories}
        concedes = {c: round(d, 3) for c, d in deltas.items() if d <= -_DELTA}
        net = (sum(1 for d in deltas.values() if d >= _DELTA)
               - sum(1 for d in deltas.values() if d <= -_DELTA))
        verdict = "chase" if net >= 0 else "trade-off"
        return WireOption(cat, pid, name, round(gain, 3), concedes, net, verdict)

    def _recent(self, store, pid, as_of, cat, season=None) -> float:
        """Recent contribution per *scheduled* game — participation-weighted, and
        volume-weighted impact for percentages, for the same reasons as the reconciler's
        shortlist (see ``Reconciler._cat_recent``)."""
        logs = store.player_logs_asof(as_of, player_id=pid)[-self.config.recent_games_window:]
        if not logs:
            return 0.0
        q = store.participation_rate(pid, as_of, window=self.config.participation_window)
        q = 1.0 if q is None else q
        if cat in PERCENTAGE_CATEGORIES:
            from fantasy_gm.valuation import league_percentage_rates

            mk, at = PERCENTAGE_CATEGORIES[cat]
            made = sum(lg.stats.get(mk, 0.0) for lg in logs)
            att = sum(lg.stats.get(at, 0.0) for lg in logs)
            if att <= 0:
                return 0.0
            league_pct = league_percentage_rates(
                store, season or self.config.primary_season).get(cat, 0.0)
            return (made / att - league_pct) * (att / len(logs)) * q
        return (sum(lg.stats.get(cat, 0.0) for lg in logs) / len(logs)) * q

    def _bundle_depth(self, store, season, wire) -> dict[str, int]:
        """Classify each available player into the bundle their production leans toward, using
        pool-mean-normalised category values so cats on different scales are comparable."""
        games = _player_games(store, season)
        pool = rosterable_pool(store, season, games=games)
        means = self._pool_means(games, pool)
        depth = {b: 0 for b in BUNDLES}
        wire_ids = {pid for pid, _ in wire}
        for pid in wire_ids:
            if pid not in games:
                continue
            prof = self._player_means(games[pid])
            scores = {b: sum(prof.get(c, 0.0) / (means.get(c, 0.0) or 1.0) for c in cats)
                      for b, cats in BUNDLES.items()}
            depth[max(scores, key=scores.get)] += 1
        return depth

    def _player_means(self, gs) -> dict[str, float]:
        out: dict[str, float] = {}
        for c in self.config.categories:
            if c in PERCENTAGE_CATEGORIES:
                mk, at = PERCENTAGE_CATEGORIES[c]
                made = sum(g.get(mk, 0.0) for g in gs)
                att = sum(g.get(at, 0.0) for g in gs)
                out[c] = made / att if att > 0 else 0.0
            else:
                out[c] = sum(g.get(c, 0.0) for g in gs) / len(gs) if gs else 0.0
        return out

    def _pool_means(self, games, pool) -> dict[str, float]:
        acc: dict[str, list[float]] = {c: [] for c in self.config.categories}
        for pid in pool:
            pm = self._player_means(games[pid])
            for c in self.config.categories:
                acc[c].append(pm[c])
        return {c: (sum(v) / len(v) if v else 0.0) for c, v in acc.items()}
