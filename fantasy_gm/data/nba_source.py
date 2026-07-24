"""Real backfill from NBA.com via ``nba_api`` (D1).

``nba_api`` is imported lazily so the rest of the package — the store, engine, log,
simulation, and the entire offline end-to-end path — runs with no network and no heavy
dependency. Only the real historical backfill needs it, and that runs locally, once
(D1). Responses are cached to disk (``RawCache``) so the backfill is resumable/idempotent.

Note: this sandbox has no NBA.com access, so this path is exercised for real only on a
developer's machine. Offline verification uses ``synthetic.seed_synthetic_season``.
"""

from __future__ import annotations

from fantasy_gm.data.cache import RawCache
from fantasy_gm.data.store import Store


def _require_nba_api():
    try:
        import nba_api  # noqa: F401
        from nba_api.stats.endpoints import leaguegamelog  # noqa: F401
    except ImportError as e:  # pragma: no cover - environment-dependent
        raise RuntimeError(
            "nba_api is required for real backfill. Install it with "
            "`pip install nba_api`. (Offline/dev flows use the synthetic season.)"
        ) from e
    return leaguegamelog


def backfill_season(store: Store, season: str, cache: RawCache) -> int:
    """Backfill games, schedules, and player box-score logs for ``season``.

    Returns the number of player-log rows written. Real network path; kept small and
    resumable via ``cache``. Season format is nba_api's ``"2025-26"``.
    """
    leaguegamelog = _require_nba_api()  # pragma: no cover - network path

    endpoint = "leaguegamelog.player"  # pragma: no cover
    params = {"season": season, "player_or_team": "P"}  # pragma: no cover
    if cache.has(endpoint, params):  # pragma: no cover
        raw = cache.get(endpoint, params)
    else:  # pragma: no cover
        raw = leaguegamelog.LeagueGameLog(
            season=season, player_or_team_abbreviation="P"
        ).get_normalized_dict()
        cache.set(endpoint, params, raw)

    # Parsing NBA.com's payload into Game/PlayerGameLog rows is deliberately left as the
    # concrete implementation for the machine that can actually reach the endpoint; the
    # shape (one row per player per game, with GAME_DATE, MATCHUP, PTS/REB/AST/...) maps
    # directly onto store.upsert_games / store.upsert_player_logs.
    raise NotImplementedError(  # pragma: no cover
        "Wire NBA.com payload parsing here on a networked machine; offline flows use "
        "fantasy_gm.data.synthetic.seed_synthetic_season."
    )
