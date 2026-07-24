"""Plain data structures shared across the data layer, engine, and log."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Game:
    """A scheduled/played game. The *schedule* (who plays when) is a priori knowledge,
    known from season start; only the *result* (scores) is effective-dated by game_date."""

    game_id: str
    season: str
    game_date: str  # ISO YYYY-MM-DD
    home_team: str
    away_team: str
    home_pts: int | None = None
    away_pts: int | None = None


@dataclass(frozen=True)
class PlayerGameLog:
    """One player's box-score line for one game. Event-dated by game_date."""

    game_id: str
    season: str
    game_date: str
    player_id: str
    player_name: str
    team: str
    stats: dict[str, float]  # keyed by category name (pts, reb, ... fg_pct, ft_pct, tov)


@dataclass(frozen=True)
class Availability:
    """An injury/availability designation, effective-dated by ``known_from``.
    ``source`` + ``confidence`` let later media enrichment coexist without overwrites."""

    player_id: str
    status: str  # ACTIVE | QUESTIONABLE | OUT
    known_from: str  # ISO date the designation became known
    source: str  # e.g. "official", "beat-writer:<name>"
    confidence: float  # 0..1
    note: str = ""


@dataclass(frozen=True)
class Matchup:
    """A weekly (or period) head-to-head pairing within a league."""

    league_id: str
    period_index: int
    period_start: str
    period_end: str
    team_a: str
    team_b: str


@dataclass(frozen=True)
class Perspective:
    """Whose decision a recommendation is made for — pins down what the engine saw."""

    league_id: str
    team_id: str
    period_index: int
    opponent_team_id: str


@dataclass
class LeagueState:
    """Point-in-time snapshot of a league as of a date: rosters for every team, the
    active matchup for the deciding team, and the per-category running tally."""

    league_id: str
    as_of: str
    lineup_cadence: str
    categories: list[str]
    is_real: bool
    rosters: dict[str, list[str]]  # team_id -> [player_id]
    active_matchup: Matchup | None
    # per-category running tally for the active matchup: {team_id: {cat: value}}
    category_tally: dict[str, dict[str, float]] = field(default_factory=dict)

    def rostered_player_ids(self) -> set[str]:
        return {pid for players in self.rosters.values() for pid in players}
