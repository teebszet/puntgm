"""Deterministic, explainable, perspective-aware skeleton engine (D5/D8).

The engine ranks waiver-wire candidates for a deciding team's upcoming scoring window.
It *accepts* full league state — including the week's matchup opponent and the per-category
tally — and surfaces winnable-category context in its reasoning. Per the round-1 scope
decision it stays a deterministic baseline: the matchup tilt is a small, configurable
nudge (default low), and full opponent-relative optimization is the next engine's job.

Every input is read through the store's as-of layer, so a recommendation for date D is a
pure function of what was known on the morning of D (plus the a priori schedule).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from fantasy_gm.config import CATEGORY_DIRECTION, Config
from fantasy_gm.engine.window import ScoringWindow, window_for
from fantasy_gm.models import LeagueState, Perspective

# Counting categories the skeleton reasons about for winnable-cat context. Percentage
# categories (fg_pct/ft_pct) need volume-weighting the next engine will add, so the
# baseline leaves them out of the tilt.
_COUNTING_CATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]


@dataclass(frozen=True)
class Recommendation:
    perspective: Perspective
    as_of: str
    league_state_ref: str
    candidate_id: str
    candidate_name: str
    rank: int
    score: float
    reasoning: str
    confidence: float


def league_state_ref(league_id: str, as_of: str, team_id: str) -> str:
    """Enough to reload the exact inputs via ``store.league_state_asof`` + perspective."""
    return f"{league_id}@{as_of}#{team_id}"


class DecisionEngine:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()

    def recommend(
        self, store, league_id: str, team_id: str, as_of: str, top_n: int = 10
    ) -> list[Recommendation]:
        meta = store.league_meta(league_id)
        if meta is None:
            raise KeyError(f"unknown league {league_id!r}")
        season = meta["season"]
        state = store.league_state_asof(league_id, as_of)
        window = window_for(state.lineup_cadence, as_of)
        perspective = self._perspective(store, state, league_id, team_id, as_of)
        winnable = self._winnable_categories(state, team_id, perspective.opponent_team_id)

        rostered = state.rostered_player_ids()
        candidates = [
            (pid, name, team)
            for pid, name, team in store.player_universe(season, as_of)
            if pid not in rostered
        ]

        scored: list[Recommendation] = []
        w = self.config.weights
        ref = league_state_ref(league_id, as_of, team_id)
        for pid, name, team in candidates:
            per_cat = self._recent_per_cat(store, pid, as_of)
            recent_prod = self._recent_production(store, pid, as_of)
            games = store.games_in_window_for_team(team, window.start, window.end)
            avail = store.availability_asof(pid, as_of)
            status = avail.status if avail else "ACTIVE"

            tilt = sum(per_cat.get(c, 0.0) * CATEGORY_DIRECTION[c] for c in winnable)
            score = (
                w.games_in_window * games
                + w.recent_production * recent_prod
                + w.winnable_category_tilt * tilt
            )
            if status == "OUT":
                score -= w.out_penalty
            elif status == "QUESTIONABLE":
                score -= w.questionable_penalty

            scored.append(
                Recommendation(
                    perspective=perspective,
                    as_of=as_of,
                    league_state_ref=ref,
                    candidate_id=pid,
                    candidate_name=name,
                    rank=0,  # assigned after sort
                    score=round(score, 4),
                    reasoning=self._reason(games, window, recent_prod, status, winnable,
                                           perspective),
                    confidence=self._confidence(games, status, len(
                        store.player_logs_asof(as_of, player_id=pid))),
                )
            )

        # deterministic ordering: score desc, then candidate id for stable ties
        scored.sort(key=lambda r: (-r.score, r.candidate_id))
        ranked = [
            Recommendation(**{**r.__dict__, "rank": i + 1})
            for i, r in enumerate(scored[:top_n])
        ]
        return ranked

    # --- helpers -------------------------------------------------------------
    def _perspective(
        self, store, state: LeagueState, league_id: str, team_id: str, as_of: str
    ) -> Perspective:
        m = store.matchup_for_team(league_id, team_id, as_of)
        if m is None:
            return Perspective(league_id, team_id, -1, "")
        opponent = m.team_b if m.team_a == team_id else m.team_a
        return Perspective(league_id, team_id, m.period_index, opponent)

    def _winnable_categories(
        self, state: LeagueState, team_id: str, opponent_id: str
    ) -> list[str]:
        """Categories currently tied or losing for the deciding team — the ones worth
        targeting this week. Empty when there's no active matchup/tally yet."""
        mine = state.category_tally.get(team_id)
        opp = state.category_tally.get(opponent_id)
        if not mine or not opp:
            return []
        winnable = []
        for c in _COUNTING_CATS:
            if c not in mine:
                continue
            direction = CATEGORY_DIRECTION[c]
            if direction * (mine[c] - opp.get(c, 0.0)) <= 0:  # tied or behind
                winnable.append(c)
        return winnable

    def _recent_logs(self, store, player_id: str, as_of: str):
        logs = store.player_logs_asof(as_of, player_id=player_id)
        return logs[-self.config.recent_games_window:]

    def _recent_production(self, store, player_id: str, as_of: str) -> float:
        logs = self._recent_logs(store, player_id, as_of)
        if not logs:
            return 0.0
        return statistics.fmean(store.fantasy_points(lg.stats) for lg in logs)

    def _recent_per_cat(self, store, player_id: str, as_of: str) -> dict[str, float]:
        logs = self._recent_logs(store, player_id, as_of)
        if not logs:
            return {}
        out: dict[str, float] = {}
        for c in _COUNTING_CATS:
            out[c] = statistics.fmean(lg.stats.get(c, 0.0) for lg in logs)
        return out

    def _reason(self, games, window: ScoringWindow, recent_prod, status, winnable,
                perspective: Perspective) -> str:
        parts = [
            f"{games} game(s) this {window.label}",
            f"recent production {recent_prod:.1f}/g",
            {"ACTIVE": "healthy", "QUESTIONABLE": "questionable", "OUT": "OUT"}.get(status, status),
        ]
        if perspective.opponent_team_id and winnable:
            parts.append(
                f"vs {perspective.opponent_team_id}: targets contested cats "
                f"{', '.join(winnable)}"
            )
        elif perspective.opponent_team_id:
            parts.append(f"vs {perspective.opponent_team_id}")
        return "; ".join(parts)

    def _confidence(self, games: int, status: str, n_logs: int) -> float:
        """Explainable, bounded confidence: more scheduled games + a healthy status +
        a real sample of games seen => higher confidence."""
        if status == "OUT":
            return 0.05
        base = min(1.0, games / 4.0)
        sample = min(1.0, n_logs / self.config.recent_games_window)
        health = 1.0 if status == "ACTIVE" else 0.6
        return round(max(0.1, base * 0.5 + sample * 0.3 + health * 0.2), 3)
