"""puntgm.com tools backend — FastAPI, deployed as a Vercel Python function.

Serves the webtools payloads (``fantasy_gm.webtools``) over a read-only snapshot of
the Mac watcher's store, behind a Yahoo sign-in gated to Tim's account. The visitor
grant is used ONLY for identity (the guid rides the token response or the id_token ``sub``);
no Fantasy API calls are made per request — every number comes from the snapshot,
which the daily watcher pushes after its 14:30 HKT run.

Deployment shape (scripts/deploy_webtools.py prepares, then verifies):

- this module imports a VENDORED copy of ``fantasy_gm`` from ``api/fantasy_gm`` —
  synced from the repo tree at deploy time, never edited in place, git-ignored so
  the copy cannot silently go stale in review;
- the snapshot rides the function bundle at ``api/store/snapshot.sqlite`` via
  ``vercel.json`` includeFiles — Vercel's filesystem is read-only, which is exactly
  the write-guarantee the store contract wants;
- secrets come from the Vercel project environment: ``SESSION_SECRET``,
  ``YAHOO_CLIENT_ID``, ``YAHOO_CLIENT_SECRET``, ``YAHOO_ALLOWED_GUID`` (Tim's
  guid), and ``YAHOO_REDIRECT_URI`` (https://www.puntgm.com/api/auth/callback).
  Unset locally, auth endpoints degrade to 503 and tool endpoints to 401 — a
  local run against the real store still serves every payload.

Routes: /api/health, /api/auth/{login,callback,logout,me}, /api/tools/{wire-lines,
fixture-grid,matchup,schedule-grid,scoreboard}. Everything except health and the
auth pair is gated.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent
if str(_API_DIR) not in sys.path:  # the vendored fantasy_gm resolves from api/
    sys.path.insert(0, str(_API_DIR))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.webtools import (
    fixture_grid_payload,
    matchup_payload,
    schedule_grid_payload,
    scoreboard_payload,
    teams_payload,
    wire_lines_payload,
)

app = FastAPI(title="puntgm tools", docs_url=None, redoc_url=None, openapi_url=None)

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
USERINFO_URL = "https://api.login.yahoo.com/openid/v1/userinfo"

SESSION_COOKIE = "puntgm_session"
STATE_COOKIE = "puntgm_oauth_state"
SESSION_DAYS = 30


# --- settings ----------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    store_path: str | None
    league_id: str
    team_id: str
    session_secret: str | None
    yahoo_client_id: str | None
    yahoo_client_secret: str | None
    yahoo_redirect_uri: str | None
    allowed_guid: str | None

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            store_path=os.environ.get("FANTASY_GM_STORE", str(_API_DIR / "store/snapshot.sqlite")),
            league_id=os.environ.get("TOOLS_LEAGUE_ID", "yahoo-478.l.25733"),
            team_id=os.environ.get("TOOLS_TEAM_ID", "t.8"),
            session_secret=os.environ.get("SESSION_SECRET"),
            yahoo_client_id=os.environ.get("YAHOO_CLIENT_ID"),
            yahoo_client_secret=os.environ.get("YAHOO_CLIENT_SECRET"),
            yahoo_redirect_uri=os.environ.get("YAHOO_REDIRECT_URI"),
            allowed_guid=os.environ.get("YAHOO_ALLOWED_GUID"),
        )

    def auth_ready(self) -> bool:
        return all((self.session_secret, self.yahoo_client_id, self.yahoo_client_secret,
                    self.yahoo_redirect_uri, self.allowed_guid))


# --- snapshot ----------------------------------------------------------------

_store: Store | None = None
_store_path: str | None = None


def get_store(settings: Settings) -> Store:
    """One Store per function instance; reopen only if the path changes."""
    global _store, _store_path
    if _store is None or _store_path != settings.store_path:
        if _store is not None:
            _store.close()
        if not settings.store_path or not Path(settings.store_path).exists():
            raise RuntimeError(f"store snapshot missing: {settings.store_path}")
        _store = Store(settings.store_path)
        _store_path = settings.store_path
    return _store


def snapshot_as_of(store: Store) -> str:
    """The snapshot's run date: the day after its newest banked box score.

    The watcher refreshes after games end, so "the morning after the last game" is
    the as-of every digest and wire read has used — derived from the data itself,
    never from this machine's clock (a serverless function's clock means nothing).
    """
    row = store.conn.execute("SELECT MAX(game_date) AS d FROM player_logs").fetchone()
    if not row or not row["d"]:
        raise RuntimeError("snapshot has no player logs")
    return (dt.date.fromisoformat(row["d"]) + dt.timedelta(days=1)).isoformat()


# --- session cookie (HMAC-signed, stdlib only) --------------------------------


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(secret: str, guid: str, now: float | None = None) -> str:
    payload = _b64(json.dumps({"sub": guid, "exp": (now or time.time()) + SESSION_DAYS * 86400}
                               ).encode())
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_session(secret: str | None, token: str | None) -> str | None:
    """The session's guid, or None (unsigned, tampered, expired — all the same door)."""
    if not secret or not token or token.count(".") != 1:
        return None
    payload, sig = token.split(".")
    expect = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        claims = json.loads(_unb64(payload))
    except ValueError:
        return None
    if not isinstance(claims, dict) or claims.get("exp", 0) < time.time():
        return None
    return claims.get("sub")


def current_guid(request: Request, settings: Settings) -> str | None:
    return read_session(settings.session_secret, request.cookies.get(SESSION_COOKIE))


def _deny(settings: Settings, request: Request, what: str) -> JSONResponse:
    if not settings.session_secret:
        return JSONResponse({"error": f"{what} unavailable: SESSION_SECRET not configured"}, 503)
    return JSONResponse({"error": f"{what}: sign in"}, 401)


# --- yahoo oauth (identity only) ----------------------------------------------


def yahoo_exchange(code: str, settings: Settings) -> dict:
    """Confidential-client code exchange. Import-time indirection so tests can patch."""
    import requests

    r = requests.post(
        TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": settings.yahoo_redirect_uri},
        auth=(settings.yahoo_client_id, settings.yahoo_client_secret),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _guid_from_id_token(token: dict) -> str | None:
    """The OIDC id_token's ``sub`` — Yahoo maps it to the account guid.

    Read without signature verification: this JWT arrives directly from
    Yahoo's token endpoint over TLS in a server-to-server exchange, so the
    signature protects nothing here, and the guid gates identity only.
    """
    id_token = token.get("id_token")
    if not id_token or id_token.count(".") < 2:
        return None
    try:
        part = id_token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return None
    sub = payload.get("xoauth_yahoo_guid") or payload.get("guid") or payload.get("sub")
    return str(sub) if sub else None


def yahoo_guid(token: dict, settings: Settings) -> str | None:
    """Guid: token-response field, then the id_token ``sub``, then userinfo."""
    guid = token.get("xoauth_yahoo_guid")
    if guid:
        return str(guid)
    guid = _guid_from_id_token(token)
    if guid:
        return guid
    import requests

    r = requests.get(USERINFO_URL, headers={
        "Authorization": f"Bearer {token.get('access_token', '')}"}, timeout=15)
    if not r.ok:
        raise RuntimeError(
            f"userinfo lookup failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json().get("sub")


# --- routes -------------------------------------------------------------------


@app.get("/api/health")
def health() -> JSONResponse:
    settings = Settings.load()
    try:
        store = get_store(settings)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, 503)
    return JSONResponse({
        "ok": True,
        "as_of": snapshot_as_of(store),
        "league_id": settings.league_id,
        "auth_ready": settings.auth_ready(),
    })


@app.get("/api/auth/login")
def auth_login() -> RedirectResponse:
    settings = Settings.load()
    if not settings.auth_ready():
        return JSONResponse({"error": "auth not configured on this deployment"}, 503)
    state = secrets.token_urlsafe(24)
    q = "&".join([
        f"client_id={settings.yahoo_client_id}",
        f"redirect_uri={settings.yahoo_redirect_uri}",
        "response_type=code",
        "scope=openid",
        f"state={state}",
    ])
    response = RedirectResponse(f"{AUTH_URL}?{q}", 303)
    response.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, secure=True,
                        samesite="lax")
    return response


@app.get("/api/auth/callback")
def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    settings = Settings.load()
    if not settings.auth_ready():
        return JSONResponse({"error": "auth not configured on this deployment"}, 503)
    if not state or state != request.cookies.get(STATE_COOKIE):
        return JSONResponse({"error": "oauth state mismatch"}, 400)
    try:
        token = yahoo_exchange(code, settings)
        guid = yahoo_guid(token, settings)
    except Exception as exc:  # noqa: BLE001 — any handshake failure is a 502 to the browser
        return JSONResponse({"error": f"yahoo exchange failed: {exc}"}, 502)
    if not guid or guid != settings.allowed_guid:
        print(f"[auth] guid gate tripped: arrived={guid!r} "
              f"allowed_len={len(settings.allowed_guid or '')}")
        return JSONResponse({
            "error": "this Yahoo account is not authorized for the tools",
            # The visitor's own account id, shown only to the browser that just
            # completed this Yahoo login (state-cookie checked) — so the gate's
            # allowlist can be set to exactly this value without server log access.
            "presented_id": guid,
        }, 403)
    print(f"[auth] session minted: guid={guid!r}")
    response = RedirectResponse("/tools/", 303)
    response.set_cookie(SESSION_COOKIE, make_session(settings.session_secret, guid),
                        max_age=SESSION_DAYS * 86400, httponly=True, secure=True,
                        samesite="lax")
    response.delete_cookie(STATE_COOKIE)
    return response


@app.get("/api/auth/logout")
def auth_logout() -> RedirectResponse:
    response = RedirectResponse("/", 303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/api/auth/me")
def auth_me(request: Request) -> JSONResponse:
    settings = Settings.load()
    guid = current_guid(request, settings)
    return JSONResponse({"signed_in": bool(guid), "guid": guid})


def _tool_payload(request: Request, build) -> JSONResponse:
    settings = Settings.load()
    if current_guid(request, settings) is None:
        # An unconfigured secret denies with 503 (deployment gap, not a login need).
        return _deny(settings, request, "tools")
    try:
        store = get_store(settings)
        return JSONResponse(build(store, settings))
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, 503)


@app.get("/api/tools/wire-lines")
def tool_wire_lines(request: Request) -> JSONResponse:
    def build(store: Store, settings: Settings):
        return wire_lines_payload(store, settings.league_id, snapshot_as_of(store))

    return _tool_payload(request, build)


@app.get("/api/tools/fixture-grid")
def tool_fixture_grid(request: Request) -> JSONResponse:
    def build(store: Store, settings: Settings):
        return fixture_grid_payload(store, settings.league_id, settings.team_id,
                                    snapshot_as_of(store))

    return _tool_payload(request, build)


@app.get("/api/tools/matchup")
def tool_matchup(request: Request) -> JSONResponse:
    def build(store: Store, settings: Settings):
        payload = matchup_payload(store, Config(), settings.league_id, settings.team_id,
                                  snapshot_as_of(store))
        if payload is None:
            return {"error": "no active matchup in the snapshot"}
        return payload

    return _tool_payload(request, build)


@app.get("/api/tools/teams")
def tool_teams(request: Request) -> JSONResponse:
    return _tool_payload(
        request, lambda store, settings: teams_payload(store, settings.league_id))


@app.get("/api/tools/schedule-grid")
def tool_schedule_grid(request: Request) -> JSONResponse:
    def build(store: Store, settings: Settings):
        return schedule_grid_payload(store, snapshot_as_of(store))

    return _tool_payload(request, build)


@app.get("/api/tools/scoreboard")
def tool_scoreboard(request: Request) -> JSONResponse:
    def build(store: Store, settings: Settings):
        payload = scoreboard_payload(store, Config(), settings.league_id,
                                     snapshot_as_of(store))
        if not payload["matchups"]:
            return {"error": "no active matchups in the snapshot"}
        return payload

    return _tool_payload(request, build)
