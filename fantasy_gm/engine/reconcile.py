"""End-of-day reconciliation (D7).

After a day's games and before that day's waiver processing, summarise the day's relevant
signals into candidate roster moves. Each move is a concrete add/drop tied to a *line of
play* for the rest of the matchup, annotated with the projected per-category impact of
making it (win-prob before → after) so the manager can compare ways to play the week out.
"""

from __future__ import annotations

from fantasy_gm.config import CATEGORY_DIRECTION, PERCENTAGE_CATEGORIES, Config
from fantasy_gm.engine.projection import Projector
from fantasy_gm.models import Perspective, ReconciliationMove


class Reconciler:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.projector = Projector(self.config)

    def reconcile(
        self, store, league_id: str, team_id: str, as_of: str, max_moves: int = 2
    ) -> list[ReconciliationMove]:
        proj = self.projector.project(store, league_id, team_id, as_of)
        if not proj.opponent_id:
            return []
        season = store.league_meta(league_id)["season"]
        matchup = store.matchup_for_team(league_id, team_id, as_of)
        state = store.league_state_asof(league_id, as_of)
        my_roster = list(state.rosters.get(team_id, []))
        rostered = state.rostered_player_ids()
        perspective = Perspective(league_id, team_id, proj.period_index, proj.opponent_id)

        from fantasy_gm.valuation import player_values
        values = player_values(store, season)  # data-derived z-value per player (A6)
        wire = self._wire_candidates(store, season, rostered, matchup.period_end, as_of)
        drops = self._drop_candidates(store, my_roster, matchup.period_end, as_of, values)
        if not wire or not drops:
            return []

        contested = set(proj.contested())
        base_wp = {c: proj.categories[c].win_prob for c in self.config.categories}
        drop = drops[0]
        if not contested:
            return []  # nothing to swing — don't churn the roster
        # Shortlist the best available add for EACH contested category (per-cat, so a
        # specialist like a FG% shooter isn't missed by a global production sort). Then
        # re-project each and keep only moves that improve a *contested* cat — never one
        # that's already safe or gone.
        cand: dict[str, tuple] = {}
        for cat in contested:
            top = sorted(wire, key=lambda w, c=cat: -self._cat_recent(store, w[0], as_of, c)
                         * CATEGORY_DIRECTION[c])[:6]
            for w in top:
                cand[w[0]] = w
        evaluated = []
        for pid, name, _rg in cand.values():
            new_roster = [p for p in my_roster if p != drop[0]] + [pid]
            after = self.projector.win_probs_for_roster(
                store, league_id, team_id, as_of, new_roster)
            deltas = {c: after.get(c, base_wp[c]) - base_wp[c] for c in self.config.categories}
            gains = {c: d for c, d in deltas.items() if c in contested and d > 0.01}
            if not gains:
                continue  # doesn't improve any category still in play
            best_cat = max(gains, key=gains.get)
            evaluated.append((gains[best_cat], best_cat, pid, name, after, deltas))

        evaluated.sort(reverse=True, key=lambda e: e[0])
        moves: list[ReconciliationMove] = []
        for gain, best_cat, pid, name, after, deltas in evaluated[:max_moves]:
            impact = {c: (round(base_wp[c], 3), round(after[c], 3))
                      for c in self.config.categories if abs(deltas[c]) >= 0.01}
            moves.append(ReconciliationMove(
                as_of=as_of, perspective=perspective,
                add_id=pid, add_name=name, drop_id=drop[0], drop_name=drop[1],
                line_of_play=f"contest {best_cat.upper()}",
                projected_impact=impact,
                confidence=round(min(1.0, max(0.1, 0.4 + gain * 3)), 3),
                drops_unplayed=self._plays_on(store, drop[0], as_of),
            ))
        return moves

    # --- candidate pools -----------------------------------------------------
    def _wire_candidates(self, store, season, rostered, period_end, as_of):
        out = []
        for pid, name, _team in store.player_universe(season, as_of):
            if pid in rostered:
                continue
            nba_team = store.player_team(pid, as_of)
            if not nba_team:
                continue
            rg = store.remaining_games_for_team(nba_team, as_of, period_end)
            if rg > 0:
                out.append((pid, name, rg))
        return out

    def _drop_candidates(self, store, my_roster, period_end, as_of, values):
        scored = []
        for pid in my_roster:
            name = self._name(store, pid, as_of)
            nba_team = store.player_team(pid, as_of)
            rg = store.remaining_games_for_team(nba_team, as_of, period_end) if nba_team else 0
            value = values.get(pid, -999.0)  # below-pool players are the first to drop
            scored.append((rg, value, pid, name))
        scored.sort()  # fewest remaining games, then lowest z-value -> best drop first
        return [(pid, name) for _rg, _value, pid, name in scored]

    # --- small helpers -------------------------------------------------------
    def _name(self, store, pid, as_of):
        row = store.conn.execute(
            "SELECT player_name FROM player_logs WHERE player_id = ? LIMIT 1", (pid,)
        ).fetchone()
        return row["player_name"] if row else pid

    def _cat_recent(self, store, pid, as_of, cat) -> float:
        """Recent per-game production in one category (volume-weighted for percentages)."""
        logs = store.player_logs_asof(as_of, player_id=pid)[-self.config.recent_games_window:]
        if not logs:
            return 0.0
        if cat in PERCENTAGE_CATEGORIES:
            mk, at = PERCENTAGE_CATEGORIES[cat]
            made = sum(lg.stats.get(mk, 0.0) for lg in logs)
            att = sum(lg.stats.get(at, 0.0) for lg in logs)
            return made / att if att > 0 else 0.0
        return sum(lg.stats.get(cat, 0.0) for lg in logs) / len(logs)

    def _plays_on(self, store, pid, as_of) -> bool:
        nba_team = store.player_team(pid, as_of)
        if not nba_team:
            return False
        row = store.conn.execute(
            """SELECT COUNT(*) n FROM games
               WHERE game_date = ? AND (home_team = ? OR away_team = ?)""",
            (as_of, nba_team, nba_team),
        ).fetchone()
        return int(row["n"]) > 0
