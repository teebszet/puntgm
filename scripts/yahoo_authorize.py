"""Get a Yahoo Fantasy access token without a client secret (SPA / public client, PKCE).

Two steps, so the browser half and the terminal half don't have to happen together:

    python scripts/yahoo_authorize.py url <client_id>     # prints a URL, saves the verifier
    python scripts/yahoo_authorize.py exchange <code>     # trades the code for a token

PKCE ties the authorization URL to a one-time secret ("verifier") that must be presented
again at exchange time, so the two steps share state via data/yahoo_pkce.json. That file
holds no long-lived credential — just the verifier and the client id — and data/ is
git-ignored.

A SPA app has no client secret by design: it is an OAuth *public client*. That is why
`yahoo_oauth`, which only implements the confidential-client flow, rejects it. Yahoo confirms
this by refusing the authorization request outright ("invalid code challenge or method")
unless PKCE parameters are present.

**Nothing in this flow tells you whether the app may actually call the Fantasy API.** Verified
the hard way on a real app: Yahoo accepted `scope=fspt-r`, ran consent, and issued a valid
access token *and* a refresh token — and every Fantasy endpoint then returned 403 "This
application is not authorized to perform this action", including public game metadata. The
authorization URL, the consent screen, and the token all succeed for an app with no Fantasy
access; the rejection lives at the API gateway.

So always finish with:

    python -m fantasy_gm.cli yahoo-check

A 403 there means the *app* lacks access, not the token. No OAuth flow can grant it —
that needs the application at https://sports.yahoo.com/developer/access/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fantasy_gm.data.yahoo_fetch import authorize_url, exchange_code

STATE = Path("data/yahoo_pkce.json")
OUT = Path("data/yahoo_access_token.txt")


def cmd_url(client_id: str, redirect_uri: str = "oob") -> int:
    url, verifier = authorize_url(client_id, redirect_uri, "code")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(
        {"client_id": client_id, "verifier": verifier, "redirect_uri": redirect_uri}))
    print("\nOpen this and sign in with the account that OWNS THE LEAGUE:\n")
    print(url)
    print("\nWatch the consent screen: it should mention Fantasy Sports access. If instead "
          "you get an error, the app lacks the fspt-r scope and needs the application at "
          "https://sports.yahoo.com/developer/access/")
    print("\nYahoo will then show a code. Bring it back with:\n")
    print("    python scripts/yahoo_authorize.py exchange <code>\n")
    return 0


def cmd_exchange(code: str, client_secret: str | None = None) -> int:
    if not STATE.exists():
        print(f"no {STATE} — run the 'url' step first (the PKCE verifier lives there)",
              file=sys.stderr)
        return 1
    st = json.loads(STATE.read_text())
    try:
        token = exchange_code(st["client_id"], code, st["verifier"],
                              st.get("redirect_uri", "oob"), client_secret)
    except Exception as exc:  # noqa: BLE001 - the raw failure is the diagnostic
        print(f"\ntoken exchange failed: {exc}\n", file=sys.stderr)
        print("invalid_grant   -> the code expired or was already used; redo the 'url' step\n"
              "invalid_client  -> Yahoo wants a secret after all; pass it as a 2nd argument\n"
              "invalid_scope / unauthorized_client -> the app lacks Fantasy permission",
              file=sys.stderr)
        return 1

    access = token.get("access_token")
    if not access:
        print(f"no access_token in response: {token}", file=sys.stderr)
        return 1
    OUT.write_text(access)
    print(f"\naccess token -> {OUT} (expires in {token.get('expires_in','?')}s)")

    # The granted scope is the one field that reveals a downgraded token, and Yahoo will
    # happily return a token whose scope is narrower than requested. Always show it.
    granted = token.get("scope")
    print(f"granted scope: {granted!r}")
    if granted is not None and "fspt" not in str(granted):
        print("  !! 'fspt-r' was NOT granted — the token cannot touch the Fantasy API.\n"
              "     Usually a redirect_uri that doesn't match one registered on the app.",
              file=sys.stderr)
    for k in ("xoauth_yahoo_guid", "token_type"):
        if k in token:
            print(f"{k}: {token[k]}")
    if token.get("refresh_token"):
        # Persisted alongside the verifier: Yahoo does hand these to public clients, and
        # re-running the browser step every hour is needless friction.
        st["refresh_token"] = token["refresh_token"]
        STATE.write_text(json.dumps(st))
        print(f"refresh token -> {STATE}")
    print("\nA valid token is NOT proof of access — the app must also be authorized for the\n"
          "Fantasy API, which only shows up as a 403 at call time. Check it now:\n")
    print("    python -m fantasy_gm.cli yahoo-check\n")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "url":
        return cmd_url(*rest[:2])
    if cmd == "exchange":
        return cmd_exchange(rest[0], rest[1] if len(rest) > 1 else None)
    print(f"unknown command {cmd!r}; expected 'url' or 'exchange'", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
