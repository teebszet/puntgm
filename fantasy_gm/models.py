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


@dataclass(frozen=True)
class UsageRole:
    """Effective-dated snapshot of a player's usage/role (D5 depth-chart cause)."""

    player_id: str
    known_from: str
    minutes: float
    fga: float
    is_starter: bool
    depth_chart_pos: int  # 1 = lead at position; higher = deeper on the bench


@dataclass(frozen=True)
class CategoryProjection:
    """Projected end-of-period outcome for one category, both sides, as a distribution."""

    category: str
    mine_total: float
    opp_total: float
    mine_std: float
    opp_std: float
    win_prob: float  # probability the deciding team wins this category
    label: str  # "safe" | "contested" | "gone"


@dataclass
class MatchupProjection:
    as_of: str
    league_id: str
    team_id: str
    opponent_id: str
    period_index: int
    categories: dict[str, CategoryProjection]

    def contested(self) -> list[str]:
        return [c for c, p in self.categories.items() if p.label == "contested"]


@dataclass(frozen=True)
class Signal:
    as_of: str
    subject_player: str
    subject_name: str
    owner_class: str  # "mine" | "opponent" | "free_agent" | "tracked"
    signal_type: str  # "usage_trend_up" | "availability_change" | "opponent_move" | ...
    evidence: str
    confidence: float
    impact: float
    relevance: float
    strength: float
    band: str  # "soft" | "strong"
    affected_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationMove:
    as_of: str
    perspective: Perspective
    add_id: str
    add_name: str
    drop_id: str
    drop_name: str
    line_of_play: str
    projected_impact: dict[str, tuple[float, float]]  # cat -> (win_prob_before, after)
    confidence: float
    drops_unplayed: bool = False


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


# --- Forward-season inputs (draft) -------------------------------------------
# All effective-dated by known_from: a draft-day read must not see a transaction
# that was only reported afterwards.


@dataclass(frozen=True)
class ForwardRoster:
    """Where a player sits going into a season that has not been played yet."""

    player_id: str
    season: str
    team: str
    depth_chart_pos: int  # 1 = lead at position; higher = deeper on the bench
    known_from: str
    role: str = ""


@dataclass(frozen=True)
class PlayerPosition:
    """A player's listed position — the input to positional slot assignment (design D4).

    Effective-dated like everything else, though position is near-static per player: what
    actually changes between reads is the *source's* opinion, not the player.
    """

    player_id: str
    position: str  # as listed: "G", "F", "C", "G-F", "F-C"
    known_from: str
    source: str = "nba"

    def slots(self) -> tuple[str, ...]:
        """Listed position split into its parts: ``"G-F"`` -> ``("G", "F")``."""
        return tuple(p for p in self.position.replace(" ", "").split("-") if p)


@dataclass(frozen=True)
class Transaction:
    """An offseason move: trade, signing, waive, or draft."""

    player_id: str
    season: str
    kind: str
    known_from: str
    from_team: str = ""
    to_team: str = ""
    note: str = ""


@dataclass(frozen=True)
class IncomingPlayer:
    """A player entering the league with no NBA game logs to project from."""

    player_id: str
    season: str
    player_name: str
    known_from: str
    draft_pick: int | None = None
    draft_team: str = ""


@dataclass(frozen=True)
class ADP:
    """Average draft position — a market observation, not a projection."""

    player_id: str
    season: str
    adp: float
    source: str
    known_from: str
    adp_std: float | None = None
    pct_drafted: float | None = None
