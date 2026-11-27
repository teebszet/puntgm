"""Read-only payloads for the puntgm.com decision tools. See payloads.py."""

from fantasy_gm.webtools.fixture_grid import fixture_grid_payload, team_defense
from fantasy_gm.webtools.payloads import (
    games_remaining,
    matchup_payload,
    scoreboard_payload,
    teams_payload,
    team_names,
)
from fantasy_gm.webtools.schedule_grid import schedule_grid_payload
from fantasy_gm.webtools.wire_lines import wire_lines_payload

__all__ = [
    "fixture_grid_payload",
    "games_remaining",
    "matchup_payload",
    "schedule_grid_payload",
    "scoreboard_payload",
    "teams_payload",
    "team_defense",
    "team_names",
    "wire_lines_payload",
]
