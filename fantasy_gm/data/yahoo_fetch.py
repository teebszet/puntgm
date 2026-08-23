"""The one credentialed step: pull a Yahoo league into a normalised offline snapshot.

Everything downstream (``yahoo_reconstruct``, ``player_crosswalk``, ``yahoo_import``) is
pure logic over the snapshot this produces, so the OAuth-dependent surface stays this
single module and the rest stays testable with no network and no secrets.

Cost is ``teams + 3`` requests, not ``teams × dates``: fetch each team's final roster once,
plus the transaction log, draft results, and the matchup schedule, then reconstruct the
point-in-time history offline. The snapshot is written to disk so a league is fetched once
and replayed against forever — which also keeps the replay harness reproducible.

Requires ``pip install -e '.[yahoo]'``. Authenticate with either a ``yahoo_oauth`` token file
(confidential client, has a secret) or a bare ``access_token`` — the latter covers apps
registered as a **SPA**, which are OAuth public clients and have no secret at all. Read-only:
this module issues no writes against the user's league.

**Unverified against the live API.** Yahoo's payloads are deeply nested and its shapes are
poorly documented, so the response-parsing here (``normalize_transactions``,
``_matchup_pairs``) is written from the published schema and has never been run against a
real token. Expect to correct it on first contact — dump a raw response and compare. The
parsing is isolated into small pure functions for exactly that reason: fixing them should
not disturb anything downstream, and ``normalize_transactions`` is unit-tested against a
recorded-shape fixture.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fantasy_gm.data.yahoo_reconstruct import Movement, Transaction

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def _require_deps():
    try:
        import yahoo_fantasy_api as yfa  # noqa: F401
        from yahoo_oauth import OAuth2  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Yahoo fetch needs the optional extra: pip install -e '.[yahoo]', then create a "
            "token file with your Yahoo app's client id/secret (see docs/yahoo.md)."
        ) from exc
    import yahoo_fantasy_api as yfa
    from yahoo_oauth import OAuth2
    return yfa, OAuth2


class TokenAuth:
    """Minimal stand-in for ``yahoo_oauth.OAuth2`` that carries a bearer token directly.

    An app registered as a **SPA** is an OAuth *public client*: it has a client id and no
    client secret by design (it uses PKCE instead). ``yahoo_oauth`` only implements the
    confidential-client flow and requires a secret, which locks that app out for no good
    reason — the Fantasy API itself only ever sees ``Authorization: Bearer <token>``.

    So accept a token from wherever it came: PKCE, implicit grant, a secret-based flow, or
    pasted by hand. A one-off league fetch is ~``teams + 3`` requests, so even a short-lived
    token with no refresh is enough — which is exactly what public clients tend to get.
    """

    def __init__(self, access_token: str):
        import requests

        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {access_token}"})

    # yahoo_fantasy_api pokes at these; a hand-supplied token is simply always "valid".
    def token_is_valid(self) -> bool:
        return True

    def refresh_access_token(self) -> None:
        raise RuntimeError(
            "This token cannot be refreshed (public/SPA client). Re-authorize and pass a "
            "fresh access token; a full league fetch takes well under a token lifetime."
        )


def authorize_url(client_id: str, redirect_uri: str = "oob",
                  response_type: str = "code") -> tuple[str, str]:
    """Build a Yahoo authorization URL and the PKCE verifier that goes with it.

    ``response_type='code'`` is the PKCE authorization-code flow (public client, no secret).
    ``response_type='token'`` requests an implicit-grant token straight back in the redirect
    fragment — fewer steps, but Yahoo's support for it is not something this repo can verify
    without credentials, so try ``code`` first and fall back.
    """
    import base64
    import hashlib
    import secrets
    from urllib.parse import urlencode

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": response_type,
        "scope": "fspt-r",  # Fantasy Sports, read-only
    }
    if response_type == "code":
        params |= {"code_challenge": challenge, "code_challenge_method": "S256"}
    return f"{AUTH_URL}?{urlencode(params)}", verifier


def exchange_code(client_id: str, code: str, verifier: str,
                  redirect_uri: str = "oob", client_secret: str | None = None) -> dict:
    """Trade an authorization code for an access token. No secret needed with PKCE."""
    import requests

    data = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(TOKEN_URL, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_access(access_token: str) -> tuple[bool, str]:
    """Is this token actually allowed to call the Fantasy API? Returns (ok, explanation).

    Worth its own function because the failure is invisible earlier in the flow. Yahoo will
    accept ``scope=fspt-r`` at the authorization endpoint, run the user through consent, and
    mint a perfectly valid access token **for an app that has no Fantasy API access** — the
    rejection only appears at the API gateway, as a 403 on every endpoint including public
    game metadata. Reaching a login page or holding a token proves nothing; this does.
    """
    import requests

    r = requests.get(
        "https://fantasysports.yahooapis.com/fantasy/v2/game/nba?format=json",
        headers={"Authorization": f"Bearer {access_token}"}, timeout=30,
    )
    if r.status_code == 200:
        return True, "Fantasy API access confirmed"
    if r.status_code == 403:
        return False, (
            "403 — the app is not authorized for the Fantasy Sports API. The token is fine; "
            "the *app* lacks access. No OAuth flow can grant it: apply at "
            "https://sports.yahoo.com/developer/access/"
        )
    if r.status_code == 401:
        return False, "401 — token expired or invalid; re-run scripts/yahoo_authorize.py"
    return False, f"unexpected HTTP {r.status_code}: {r.text[:200]}"


def normalize_transactions(raw: list[dict[str, Any]]) -> list[Transaction]:
    """Convert Yahoo's transaction payload into provider-agnostic movements.

    Yahoo represents each transaction as a list of player entries carrying a
    ``transaction_data`` block with ``type`` (add/drop) and source/destination team keys.
    A single add/drop transaction contains two such entries; a trade contains one per
    player moved. Timestamps are epoch seconds and are reduced to the local ISO date,
    which is the granularity the store's as-of layer keys on.
    """
    from datetime import UTC, datetime

    out: list[Transaction] = []
    for tx in raw:
        ts = tx.get("timestamp")
        when = (datetime.fromtimestamp(int(ts), UTC).date().isoformat() if ts else
                tx.get("date", ""))
        movements: list[Movement] = []
        for p in tx.get("players", []):
            data = p.get("transaction_data") or {}
            if isinstance(data, list):           # Yahoo sometimes wraps it in a list
                data = data[0] if data else {}
            kind = data.get("type")
            src = data.get("source_team_key")
            dst = data.get("destination_team_key")
            pid = str(p.get("player_id", ""))
            if not pid:
                continue
            if kind == "add":
                movements.append(Movement(pid, from_team=None, to_team=dst))
            elif kind == "drop":
                movements.append(Movement(pid, from_team=src, to_team=None))
            else:                                 # trade / commish move
                movements.append(Movement(pid, from_team=src, to_team=dst))
        if movements:
            out.append(Transaction(when, tx.get("type", "unknown"), movements))
    return out


def fetch_league_snapshot(
    league_key: str, token_path: str | None = None, season: str = "",
    out_path: str | None = None, transaction_count: int = 2000,
    access_token: str | None = None,
) -> dict[str, Any]:
    """Fetch one league into a snapshot dict (and optionally write it to ``out_path``).

    Authenticate either way:

    * ``token_path`` — a ``yahoo_oauth`` token file (confidential client, has a secret).
    * ``access_token`` — a bearer token obtained however you like, including from a SPA /
      public client that has no secret at all. See ``TokenAuth``.

    ``transaction_count`` is set high deliberately: Yahoo paginates transactions and a
    truncated log silently corrupts the reconstructed roster history (see
    ``yahoo_reconstruct``), so we ask for far more than a season can contain and let the
    draft-results check confirm we got them all.
    """
    yfa, OAuth2 = _require_deps()
    if access_token:
        oauth = TokenAuth(access_token)
    elif token_path:
        oauth = OAuth2(None, None, from_file=token_path)
        if not oauth.token_is_valid():
            oauth.refresh_access_token()
    else:
        raise ValueError("pass either token_path= or access_token=")

    lg = yfa.League(oauth, league_key)
    settings = lg.settings()
    teams = lg.teams()

    team_ids = list(teams.keys())
    final_rosters: dict[str, list[str]] = {}
    for tid in team_ids:
        roster = yfa.Team(oauth, tid).roster()
        final_rosters[tid] = [str(p["player_id"]) for p in roster]

    draft: dict[str, list[str]] = {}
    player_names: dict[str, str] = {}
    for pick in lg.draft_results():
        draft.setdefault(str(pick["team_key"]), []).append(str(pick["player_id"]))

    # names for every player that appears anywhere in the league's history
    all_ids = {p for r in final_rosters.values() for p in r}
    all_ids |= {p for r in draft.values() for p in r}
    raw_txns = lg.transactions("add,drop,trade", transaction_count)
    transactions = normalize_transactions(raw_txns)
    all_ids |= {m.player_id for t in transactions for m in t.movements}
    for chunk in _chunks(sorted(all_ids), 25):
        for det in lg.player_details([int(p) for p in chunk]):
            player_names[str(det["player_id"])] = det.get("name", {}).get("full", "")

    matchups = []
    for week in range(1, int(lg.end_week()) + 1):
        start, end = lg.week_date_range(week)
        seen: set[frozenset] = set()
        for m in _matchup_pairs(lg, week):
            if frozenset(m) in seen:
                continue
            seen.add(frozenset(m))
            matchups.append({"period": week, "start": str(start), "end": str(end),
                             "team_a": m[0], "team_b": m[1]})

    snapshot = {
        "league_id": f"yahoo-{league_key}",
        "league_key": league_key,
        "name": settings.get("name", league_key),
        "season": season,
        "cadence": "daily-change",
        "teams": [{"team_id": tid, "name": t.get("name", tid)} for tid, t in teams.items()],
        "player_names": player_names,
        "final_rosters": final_rosters,
        "draft_results": draft,
        "transactions": [asdict(t) for t in transactions],
        "matchups": matchups,
    }
    if out_path:
        with open(out_path, "w") as fh:
            json.dump(snapshot, fh, indent=2)
    return snapshot


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _matchup_pairs(lg, week) -> list[tuple[str, str]]:
    """Extract (team_a, team_b) pairs from Yahoo's nested matchup payload."""
    pairs: list[tuple[str, str]] = []
    raw = lg.matchups(week)
    try:
        scoreboard = raw["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]
    except (KeyError, IndexError, TypeError):
        return pairs
    for key, m in scoreboard.items():
        if not key.isdigit():
            continue
        try:
            teams = m["matchup"]["0"]["teams"]
            a = teams["0"]["team"][0][0]["team_key"]
            b = teams["1"]["team"][0][0]["team_key"]
            pairs.append((a, b))
        except (KeyError, IndexError, TypeError):
            continue
    return pairs


def load_snapshot(path: str) -> dict[str, Any]:
    with open(path) as fh:
        return json.load(fh)
