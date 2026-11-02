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

import statistics
from dataclasses import dataclass
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


# --- injury-driven usage-spike watch -----------------------------------------

# A designation this fresh counts as "injury news": its minutes are up for grabs.
SPIKE_NEWS_DAYS = 14
# A candidate matters when his recent minutes sit at least this far below the injured
# player's — room to absorb, not a co-starter.
SPIKE_MIN_GAP = 8.0
# The injured player must have actually logged these minutes in his last games for
# "minutes available" to mean anything.
SPIKE_MIN_INJURED_MIN = 16.0


@dataclass(frozen=True)
class SpikeCandidate:
    player_id: str
    name: str
    position: str
    minutes_per_game: float   # the player's own recent-5-games average
    is_starter: bool
    minutes_gap: float        # injured player's recent minutes − this player's


@dataclass(frozen=True)
class UsageSpike:
    injured_player_id: str
    injured_name: str
    injured_team: str
    injured_status: str       # OUT | QUESTIONABLE
    injured_minutes: float    # the injured player's recent-5-games average
    candidates: tuple[SpikeCandidate, ...]


@dataclass(frozen=True)
class SpikeWatch:
    """League-wide: every rostered player with an OUT designation (fresh injury) and
    the same-position teammates who could absorb his minutes."""
    as_of: str
    spikes: list[UsageSpike]


def injury_usage_spikes(store, league_id: str, as_of: str,
                        *, max_candidates: int = 3) -> SpikeWatch:
    """The usage-spike watch: who absorbs an injured teammate's minutes?

    An OUT designation is unpriced opportunity for the rest of that NBA team — the box
    score cannot say who takes the minutes, but it does say who plays the same position
    and how many minutes each currently sits on. The watch is:

    1. every rostered player with an OUT designation, dated within ``SPIKE_NEWS_DAYS``,
       whose recent-5-games minutes are real (``SPIKE_MIN_INJURED_MIN``);
    2. his NBA-team teammates listed at the same position (overlap on any shared slot,
       so "G-F" matches "F" or "G") who are not OUT themselves;
    3. ranked by CURRENT minutes — the next man up: the most-established teammate who
       still has real room (the ≥ ``SPIKE_MIN_GAP`` gap filter stays as the room gate) —
       capped at ``max_candidates``.

    The output names the *watch*, not a projection: the engine does not yet propagate an
    injury's minutes to a teammate's projection (each player is projected from his own
    trailing distribution), so this is the read that surfaces who to watch, not a
    projection that re-prices him.
    """
    state = store.league_state_asof(league_id, as_of)
    rostered = set().union(set(), *(set(v) for v in state.rosters.values()))
    season = store.latest_logged_season() or store.league_meta(league_id)["season"]
    positions = store.player_positions_asof(as_of)
    # Names across every logged season, not just the latest: a player whose only rows
    # are 2025-26 (the 2026-27 backfill is still filling) is exactly the kind of injured
    # veteran who was printing as a bare Yahoo id (found live 2026-10-29).
    names = store.player_names(as_of)
    today = date.fromisoformat(as_of)

    # Rostered players currently OUT, with real recent minutes.
    out: list[tuple[str, float]] = []
    for pid in sorted(rostered):
        avail = store.availability_asof(pid, as_of)
        if avail is None or avail.status != "OUT":
            continue
        try:
            if (today - date.fromisoformat(avail.known_from)).days > SPIKE_NEWS_DAYS:
                continue
        except ValueError:
            continue
        hist = store.usage_role_history(pid, as_of)
        if len(hist) < 2:
            continue
        recent = statistics.fmean([u.minutes for u in hist[-5:]])
        if recent < SPIKE_MIN_INJURED_MIN:
            continue
        out.append((pid, recent))

    # Teammates by NBA team, read once: player_universe's team column is the last logged
    # team, so a player whose current team differs is re-checked against player_team.
    team_ids_by_team: dict[str, list[str]] = {}
    for pid2, _n, t in store.player_universe(season, as_of):
        team_ids_by_team.setdefault(t, []).append(pid2)

    spikes: list[UsageSpike] = []
    for pid, injured_min in out:
        team = store.player_team(pid, as_of)
        if not team:
            continue
        inj_pos = positions.get(pid)
        if inj_pos is None or not inj_pos.position:
            continue
        inj_slots = set(inj_pos.position.split("-"))
        candidates: list[SpikeCandidate] = []
        for pid2 in team_ids_by_team.get(team, []):
            if pid2 == pid:
                continue
            avail2 = store.availability_asof(pid2, as_of)
            if avail2 is not None and avail2.status == "OUT":
                continue
            pos2 = positions.get(pid2)
            if pos2 is None or not pos2.position:
                continue
            if not (inj_slots & set(pos2.position.split("-"))):
                continue
            hist2 = store.usage_role_history(pid2, as_of)
            if len(hist2) < 2:
                continue
            mins2 = statistics.fmean([u.minutes for u in hist2[-5:]])
            gap = injured_min - mins2
            if gap < SPIKE_MIN_GAP:
                continue
            candidates.append(SpikeCandidate(
                pid2, names.get(pid2, pid2), pos2.position, round(mins2, 1),
                hist2[-1].is_starter, round(gap, 1)))
        # Next man up: rank by CURRENT minutes. Minutes flow up the rotation, not to
        # the deepest benchwarmer — ranked by minutes_gap until 2026-11-02, which led
        # with 4-5 min benchwarmers whose theoretical +29 "room" was last-in-rotation
        # fiction, ahead of the 23-min sixth man who actually inherits the minutes
        # (Tim, "a bit off on most cases"). The SPIKE_MIN_GAP filter above still
        # guarantees every candidate has real room to gain.
        candidates.sort(key=lambda c: -c.minutes_per_game)
        spikes.append(UsageSpike(pid, names.get(pid, pid), team, "OUT",
                                 round(injured_min, 1), tuple(candidates[:max_candidates])))
    return SpikeWatch(as_of, spikes)
