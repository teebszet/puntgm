"""Signal detection and strength grading (D5–D6).

Signals are typed observations (usage trend, availability change, opponent move) about a
player relevant to a deciding team. Display strength combines three things:

    strength = confidence × impact-on-a-contested-category × relevance-to-my-build

A signal is *strong* only when it is sustained, has an identifiable depth-chart cause, and
clears the strength bar; otherwise it stays *soft* (a one-week mirage or an unsustainable
heater never gets promoted). Relevance is situational: it depends on whose player it is,
which categories are live in this matchup, and the stage of the season.
"""

from __future__ import annotations

from datetime import date

from fantasy_gm.config import (
    EARLY_STAGE_MAX,
    LATE_STAGE_MIN,
    STRONG_STRENGTH,
    Config,
)
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import MatchupProjection, Signal

_COUNTING_CATS = ["pts", "reb", "ast", "stl", "blk", "fg3m", "tov"]


def season_stage(store, season: str, as_of: str) -> str:
    row = store.conn.execute(
        "SELECT MIN(game_date) lo, MAX(game_date) hi FROM games WHERE season = ?", (season,)
    ).fetchone()
    if not row or row["lo"] is None:
        return "mid"
    lo = date.fromisoformat(row["lo"])
    hi = date.fromisoformat(row["hi"])
    d = date.fromisoformat(as_of)
    span = (hi - lo).days or 1
    frac = (d - lo).days / span
    if frac <= EARLY_STAGE_MAX:
        return "early"
    if frac >= LATE_STAGE_MIN:
        return "late"
    return "mid"


class SignalEngine:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.projector = Projector(self.config)

    def detect(self, store, league_id: str, team_id: str, as_of: str) -> list[Signal]:
        meta = store.league_meta(league_id)
        if meta is None:
            raise KeyError(f"unknown league {league_id!r}")
        season = meta["season"]
        state = store.league_state_asof(league_id, as_of)
        proj = self.projector.project(store, league_id, team_id, as_of)
        contested = set(proj.contested())
        stage = season_stage(store, season, as_of)

        my_players = set(state.rosters.get(team_id, []))
        opp_players = set(state.rosters.get(proj.opponent_id, [])) if proj.opponent_id else set()

        signals: list[Signal] = []
        for pid, name, _team in store.player_universe(season, as_of):
            owner = ("mine" if pid in my_players
                     else "opponent" if pid in opp_players else "free_agent")
            sig = self._usage_trend(store, pid, name, owner, as_of, contested, stage)
            if sig:
                signals.append(sig)

        signals.extend(self._opponent_moves(store, proj, state, as_of))
        signals.sort(key=lambda s: -s.strength)
        return signals

    # --- usage trend ---------------------------------------------------------
    def _usage_trend(self, store, pid, name, owner, as_of, contested, stage) -> Signal | None:
        hist = store.usage_role_history(pid, as_of)
        if len(hist) < 2:
            return None
        recent, prev = hist[-1], hist[-2]
        if recent.minutes - prev.minutes <= 1.0:
            return None  # no upward usage move
        sustained = len(hist) >= 3 and hist[-1].minutes > hist[-2].minutes > hist[-3].minutes
        causal = recent.depth_chart_pos < hist[0].depth_chart_pos or (
            recent.is_starter and not hist[0].is_starter
        )
        confidence = 0.3 + (0.3 if sustained else 0.0) + (0.25 if causal else 0.0)
        confidence = min(1.0, confidence + min(0.15, (recent.minutes - prev.minutes) / 40.0))

        top = self._top_cats(store, pid, as_of)
        helps_contested = bool(set(top) & contested)
        impact = 1.0 if helps_contested else 0.5

        owner_w = {"mine": 1.0, "opponent": 0.7,
                   "free_agent": 1.0 if helps_contested else 0.4}[owner]
        stage_w = {"early": 1.3, "mid": 1.0, "late": 0.8}[stage]
        relevance = min(1.0, owner_w * stage_w)

        strength = round(confidence * impact * relevance, 4)
        band = "strong" if (strength >= STRONG_STRENGTH and sustained and causal) else "soft"
        cause = " (depth-chart move up)" if causal else ""
        evidence = (f"minutes {prev.minutes:.0f}→{recent.minutes:.0f}"
                    f"{', sustained' if sustained else ''}{cause}")
        return Signal(as_of, pid, name, owner, "usage_trend_up", evidence,
                      round(confidence, 3), impact, round(relevance, 3), strength, band,
                      tuple(top))

    def _top_cats(self, store, pid, as_of, n: int = 2) -> list[str]:
        dist = store.player_distribution(pid, as_of, _COUNTING_CATS,
                                         window=self.config.recent_games_window)
        # rank counting cats by mean; turnovers excluded from "strengths"
        ranked = sorted(
            (c for c in _COUNTING_CATS if c != "tov"),
            key=lambda c: -dist[c][0],
        )
        return ranked[:n]

    # --- opponent move -------------------------------------------------------
    def _opponent_moves(self, store, proj: MatchupProjection, state, as_of) -> list[Signal]:
        if not proj.opponent_id:
            return []
        matchup = store.matchup_for_team(proj.league_id, proj.team_id, as_of)
        if matchup is None:
            return []
        events = store.roster_events_between(
            proj.league_id, proj.opponent_id, matchup.period_start, as_of
        )
        if not events:
            return []
        added_cats, dropped_cats = set(), set()
        for e in events:
            cats = set(self._top_cats(store, e["player_id"], as_of))
            (added_cats if e["action"] == "add" else dropped_cats).update(cats)
        if not (added_cats or dropped_cats):
            return []
        evidence = (f"opponent added strength in {sorted(added_cats) or '—'}, "
                    f"dropped {sorted(dropped_cats) or '—'} → targeting {sorted(added_cats)}, "
                    f"conceding {sorted(dropped_cats)}")
        return [Signal(as_of, "", f"opponent {proj.opponent_id}", "opponent", "opponent_move",
                       evidence, 0.8, 1.0, 0.8, round(0.8 * 1.0 * 0.8, 4), "strong",
                       tuple(sorted(added_cats | dropped_cats)))]
