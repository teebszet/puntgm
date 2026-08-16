# Importing a real Yahoo league

The point of this path is that a real league is both the **validation gold standard** (the
replay harness stops depending on simulated opponents) and the **BYOT product's data
intake** — same code, two uses.

Only step 2 needs credentials. Everything after it is offline, deterministic, and covered
by tests, so the risky part is small and isolated.

## 1. Apply for Fantasy API access (NOT self-serve — expect a wait)

**The Fantasy Sports checkbox no longer exists on <https://developer.yahoo.com/apps/create/>.**
If you create an app there you will see only whatever APIs your account's region exposes (a
Taiwan account, for instance, shows "TW Auction"). That is not a regional restriction on
Fantasy — Yahoo moved Fantasy Sports out of self-serve entirely.

Access is now **application-gated with manual review**: <https://sports.yahoo.com/developer/access/>

Notes that matter for the application:

- Yahoo's form explicitly asks whether access is limited to **personal or single-league use**.
  Say yes if it's true — that's by far the easiest thing for them to approve, and it's what
  the replay validation actually needs. Applying as a commercial product invites a much
  harder review.
- Be specific. The portal warns that *"incomplete or insufficiently detailed submissions
  cannot be evaluated and will be closed without further correspondence."*
- **Read-only is the default**, which is all this repo ever needs — say so.
- If you already have any Yahoo Developer Network app, supply its **Client ID** on the form;
  Yahoo says that expedites provisioning. The TW Auction app you already made counts.
- Turnaround time is not published. Plan around that.

Once approved, put the credentials in `data/yahoo_token.json`:

```json
{"consumer_key": "<client id>", "consumer_secret": "<client secret>"}
```

`data/` is git-ignored, and `*.secret`/`.env` are too — don't move this file elsewhere.

> **Product risk, not just a setup annoyance.** Anything that syncs a user's Yahoo league —
> including the hosted/BYOT product — depends on Yahoo approving *that* use case, and they
> review it. Worth knowing before the roadmap leans on Yahoo sync. The blast radius here is
> small by design: only `yahoo_fetch.py` is Yahoo-specific, while `yahoo_reconstruct.py`,
> `player_crosswalk.py`, and `import_snapshot` all operate on a normalised snapshot dict and
> would serve another provider — or a hand-built snapshot — unchanged.

### Already have an older app? Test it — don't trust the UI or the OAuth flow

An app predating the self-serve cutoff *might* hold Fantasy permission. There is only one
reliable way to find out, and it is not the app's permission list and not whether OAuth
succeeds:

```bash
python scripts/yahoo_authorize.py url <client_id>   # open URL, sign in, get code
python scripts/yahoo_authorize.py exchange <code>
python -m fantasy_gm.cli yahoo-check                # <- the actual test
```

**A complete, successful OAuth flow proves nothing.** Measured on a real pre-cutoff app
(2026-08-16): Yahoo accepted `scope=fspt-r`, ran the consent screen, and issued a valid access
token *and* a refresh token — then returned **403 "This application is not authorized to
perform this action"** on every Fantasy endpoint, including public game metadata that needs no
user data at all. The app-level check lives at the API gateway and nowhere earlier. Probing
the authorize endpoint is equally useless: `fspt-r`, `openid`, and *no scope at all* all
redirect to the login page identically.

Notably, that app's console **did** show `Fantasy Sports - Read` ticked, and the redirect URI
was valid (`oob` is accepted; an unregistered URL is properly rejected with `invalid redirect
uri`). So a ticked permission box is not proof of API access either — plausibly the console
still renders a legacy checkbox while the gateway enforces the post-cutoff allowlist.

**Resolved (2026-08-16): it is gateway allowlisting.** Everything else was eliminated — the
permission box is ticked, the redirect URI is valid, PKCE is correct, and the token is
demonstrably parsed by Yahoo (calling over plain `http` returns `401
bearer_token_not_over_ssl`, i.e. the token was read and rejected for an unrelated reason).
Yahoo returns no `scope` field at all on the token response, so that field cannot be used to
diagnose this. A pre-cutoff app therefore **cannot** be revived: apply above.

`yahoo_authorize.py` still prints the granted scope, which is worth keeping for the general
case of a downgraded token — just don't expect it to be populated here.

**A missing client secret is not a problem.** An app registered as a *SPA* is an OAuth
**public client** — it has a client id and no secret by design, and uses PKCE instead. The
`yahoo_oauth` library only implements the confidential-client flow and demands a secret, which
is why it rejects such an app; the Fantasy API itself only ever sees a bearer token. Use:

```bash
python scripts/yahoo_authorize.py url <client_id> [redirect_uri]
python scripts/yahoo_authorize.py exchange <code>
```

which runs the PKCE flow and writes `data/yahoo_access_token.txt`. Yahoo **requires** PKCE for
a public client — without `code_challenge` it refuses the authorization request outright with
"invalid code challenge or method". `oob` works as a redirect URI, so you don't need to host
anything; the registered redirect also works if you pass it exactly.

If it fails with `invalid_client`, Yahoo wants a secret after all — pass one as a second
argument to `exchange`.

## 2. Fetch the league snapshot (needs credentials + network)

```bash
pip install -e '.[yahoo]'

# with a token file (confidential client)
python -c "
from fantasy_gm.data.yahoo_fetch import fetch_league_snapshot
fetch_league_snapshot('454.l.12345', 'data/yahoo_token.json', '2025-26',
                      out_path='data/yahoo-454.l.12345.json')"

# or with a bare access token (SPA / public client)
python -c "
from fantasy_gm.data.yahoo_fetch import fetch_league_snapshot
fetch_league_snapshot('454.l.12345', season='2025-26',
                      access_token=open('data/yahoo_access_token.txt').read().strip(),
                      out_path='data/yahoo-454.l.12345.json')"
```

The first run opens a browser for OAuth consent and caches the token. Your **league key**
is in the league URL (`basketball.fantasysports.yahoo.com/nba/12345` → the key is
`<game_id>.l.12345`; the game id for a season is visible in any API response, or use
`yahoo_fantasy_api.Game(oauth, 'nba').league_ids()`).

Cost is `teams + 3` requests, not `teams × dates` — the season's roster history is
reconstructed offline from the transaction log rather than snapshotted day by day.

> **Expect to fix the parsers on first contact.** `normalize_transactions` and
> `_matchup_pairs` in `yahoo_fetch.py` are written from Yahoo's published schema but have
> never run against a live token, and Yahoo's JSON is famously nested. Dump a raw response
> and compare. They're small pure functions with unit tests, so corrections don't ripple.

## 3. Import it (offline)

```bash
python -m fantasy_gm.cli yahoo-import data/yahoo-454.l.12345.json
```

The import **refuses** rather than degrades in two cases, because both corrupt the replay
silently rather than merely reducing coverage:

- **Unresolved players.** Yahoo player ids are joined to NBA.com ids by normalised name
  (`player_crosswalk.py`), which absorbs accents, punctuation, and `Jr./III` suffixes.
  Genuine nickname divergence ("Nic" vs "Nicolas") lands in `unmatched` rather than being
  fuzzy-matched onto the wrong player. Resolve them once:

  ```bash
  python -m fantasy_gm.cli yahoo-import snap.json --overrides data/yahoo_overrides.json
  # {"6018": "1630163", ...}   yahoo id -> nba id
  ```

- **Unverifiable roster history.** Roster state is rebuilt by rewinding the transaction log
  from each team's final roster back to draft day, then replaying forward as add/drop
  events. That rewind is checked against Yahoo's own `draft_results`. **This check is
  mandatory, not cosmetic:** Yahoo paginates transactions, and a log truncated at the
  oldest end leaves every remaining undo internally consistent — it produces a wrong season
  with no other symptom. The draft comparison is the only thing that catches it.

`--force` loads anyway and records provenance describing exactly what's wrong. Use it to
eyeball a league, never to produce a published number.

## 4. Replay against it

```bash
python -m fantasy_gm.cli compare yahoo-454.l.12345
```

One real league is roughly 25 decision slots per team — too few for a standalone claim, but
it's the credibility anchor: it's the only setting where opponents actually managed their
rosters. Keep using pooled simulated leagues for statistical power and report the real
league separately rather than averaging the two together.

**Grade real leagues on the opponent-independent metric only.** "Would this move have won
the week" is contaminated on a real league — the actual manager made other moves that
period, so the stand-pat counterfactual isn't clean. Whether the add out-produced the drop
in the target category stays valid.
