"""Draft-watch web service: one page to start the watcher, live recs on the same page.

Why: Tim asked (fantasy-nba-gm, 2026-10-06) for a page he can submit the mock-draft
inputs on -- and for the page itself to show the live recommendations as the watcher
makes them. The page is served through Tailscale Funnel (token path, same pattern as
the evidence pages); submission arms a launchd runner that starts this service in
watcher mode; the page then polls /state.json and renders picks, discrepancies and
on-the-clock recommendations.

One process, two modes:

* ``... serve <dir> <port> <state.json>`` -- static file server (Range support) plus
  GET /state.json read from the watcher's state file; this is the page.

* ``... watch <league> --seat N --interval S --out <state.json> [--serve-port P]``
  -- the watcher loop, plus a localhost HTTP server answering /state.json from memory
  (dev/testing convenience only: binds 127.0.0.1; the Funnel proxy reaches the page
  through the separate ``serve`` process reading the same state file, so the public
  page viewer can never execute anything). The watcher detaches into its own session
  (setsid) at startup and records its pid in the state file, so re-arming can find and
  replace it precisely.

* ``... reset-state --out <state.json> --league <key> [--seat N] [--interval S]
  [--start UTC]`` -- runner helper: overwrite the state file with a fresh 'armed'
  state (no picks, no recs), killing nothing.

Arm lifecycle (2026-10-08 redesign): clicking Arm makes the serve process kill every
watcher and reset the state file immediately, then leave trigger.json for the launchd
runner, which kills any survivors and resets the state again before spawning the new
watcher. Exactly one watcher exists at any time, and the page never shows a previous
draft's picks after an arm.

Everything the watcher knows lands in the state file after every change: picks in
order, discrepancy lines (never silently reconciled -- see live.reconcile), the latest
recommendation as structured candidates, and a heartbeat. The page shows exactly what
the terminal watcher would print.
"""

from __future__ import annotations

import importlib.resources
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- shared state file helpers ---------------------------------------------------

STATE_VERSION = 1

# pgrep/ps marker identifying watcher processes (not serve, not the page).
WATCHER_MARKER = "draft_watch_service.py watch"

# How many players the rec card and each punt build carry (Tim, draft day 2026-10-17):
# both page lists scroll in a ~20-row viewport, so the state must carry enough rows
# to fill it and leave something to scroll to.
PAGE_TOP_N = 24


def nba_roster_moves(store, today: str, cache, forward_season: str) -> tuple[int, int, int]:
    """One NBA.com wire cycle (Tim asked for this source by name, 2026-10-15).

    A fresh ``playerindex`` fetch (``refresh=True`` bypasses the disk cache — a wire
    that re-reads yesterday's capture never sees today's move), parsed and diffed
    against the roster stored *as of today*. Returns ``(new events stored, rows
    fetched, players the roster fold re-priced)``; raises when the fetch fails so the
    caller logs a dead wire loudly. Every capture is written back to the disk cache,
    so any reported move stays auditable offline.

    The fold (task 9.11) is the pricing half the events alone never were: when the
    league moved, the forward-season inputs are rewritten dated today, so the rebuilt
    board's minutes model ranks moved players on their NEW team's depth. Events-only
    was the interim state Tim corrected the same day: the board must reflect the most
    recent lineups, not merely carry their headlines.
    """
    from fantasy_gm.data.player_index import _fetch_rows, fold_forward_roster, parse_player_index
    from fantasy_gm.projections.news import roster_move_events

    entries = parse_player_index(_fetch_rows(forward_season, cache, refresh=True))
    before = store.forward_roster_map_asof(forward_season, today)
    events = roster_move_events(before, entries, today)
    new = store.add_news_events(events) if events else 0
    fold = fold_forward_roster(store, entries, forward_season, today, as_of=today)
    return new, len(entries), fold["changed_players"]


def yahoo_credentialed_refresh(
    store, league: str, today: str
) -> tuple[list[str], bool]:
    """One credentialed Yahoo pass: player statuses + every team's scheduled lineup.

    The lineup rows (``lineup_slots``) are what the projector now prices off — win
    projections must use Yahoo's scheduled lineups, not assume the whole roster plays
    (Tim, 2026-10-23). The raw payloads are written under ``data/raw_cache/`` so any
    stored row is auditable offline.

    On a 401 the access token is refreshed once from the refresh-token files
    (``refresh_token_from_files`` persists the rotation) and the pass retries — the
    token expires every few days and a dead token used to leave the credentialed wires
    silently off until a manual re-auth.

    Returns ``(log lines, changed)``; raises when both attempts fail.
    """
    from fantasy_gm.data.yahoo_fetch import (
        fetch_league_lineups,
        refresh_token_from_files,
    )
    from fantasy_gm.data.yahoo_import import ingest_lineups
    from fantasy_gm.projections.status import fetch_league_player_metadata, refresh_statuses

    token_path = Path("data/yahoo_access_token.txt")
    lines: list[str] = []
    changed = False

    def _pass() -> None:
        nonlocal changed
        token = token_path.read_text().strip()
        rows = fetch_league_player_metadata(
            league, token, save_path="data/raw_cache/yahoo_status_live.json"
        )
        ref = refresh_statuses(store, rows, today)
        for name, old, new in ref.changed:
            lines.append(f"status: {name} {old or 'none'} -> {new or 'none'}")
        if ref.changed:
            changed = True
        slots = fetch_league_lineups(
            league, token, save_path="data/raw_cache/yahoo_lineups_live.json"
        )
        report = ingest_lineups(store, f"yahoo-{league}", slots, today)
        if report["stored"]:
            lines.append(
                f"yahoo lineups: +{report['stored']} slot row(s) stored "
                f"({report['matched']} players matched)"
            )
            changed = True
        for pid, name in report["unmatched"]:
            lines.append(f"yahoo lineups: UNMATCHED {name} (yahoo {pid}) — priced at zero")
        for pid, name, hits in report["ambiguous"]:
            lines.append(f"yahoo lineups: AMBIGUOUS {name} (yahoo {pid}) -> {hits}")

    try:
        _pass()
    except RuntimeError as exc:
        if "401" not in str(exc):
            raise
        refresh_token_from_files()
        lines.append("yahoo access token refreshed from refresh_token")
        _pass()
    return lines, changed


def new_state(
    league: str,
    seat: int,
    interval: float,
    started_at: str,
    start_utc: str | None = None,
    status: str = "starting",
) -> dict:
    return {
        "version": STATE_VERSION,
        "league": league,
        "seat": seat,
        "interval_s": interval,
        "status": status,
        "started_at": started_at,
        "updated_at": started_at,
        "start_utc": start_utc,
        "board_players": None,
        "pick_count": 0,
        "picks": [],
        "issues": [],
        "events": [],
        "recommendation": None,
        "differential": None,
        "punt_panel": None,
        "news_sources": None,
        "error": None,
        "heartbeat_at": started_at,
        "watcher_pid": None,
    }


def load_state(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def write_state(path: str | Path, state: dict) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Thread-unique tmp name: the news refresher thread writes this same state file
    # concurrently with the poll loop, and two writers sharing one tmp path could
    # interleave into a corrupt replace.
    tmp = Path(f"{path}.w{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def log_line(state: dict, text: str) -> None:
    """Append an event line; the page renders these in order."""
    state["events"].append({"at": time.strftime("%H:%M:%S", time.gmtime()), "text": text})
    del state["events"][:-200]


# --- arm-time helpers: fresh state + watcher cleanup -------------------------------


def reset_state(
    state_file: str | Path, league: str, seat: int, interval: float, start_utc: str | None = None
) -> dict:
    """Overwrite the state file with a fresh 'armed' state for a new draft.

    Arming must clear the previous draft's picks immediately (2026-10-08: the page
    kept showing the last draft's 153 picks until the new watcher's first write --
    which, before the setsid fix, never came). Call this the moment an arm is
    accepted, before the watcher starts.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = new_state(league, seat, interval, now, start_utc=start_utc, status="armed")
    when = f", starts {start_utc} UTC" if start_utc else ", starting within a minute"
    log_line(state, f"armed: league {league} seat {seat}{when} — previous draft cleared")
    # A leftover punt request must not leak into the new draft (same hygiene as trigger).
    Path(state_file).with_name("punt.json").unlink(missing_ok=True)
    write_state(state_file, state)
    return state


def _pid_cmdline(pid: int) -> str:
    try:
        out = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_watchers(state_file: str | Path, grace: float = 2.0) -> list[int]:
    """Terminate every watcher process; return the pids that were signalled.

    Candidates come from two places so neither can be fooled alone: the recorded
    ``watcher_pid`` in the state file (exact process this page watched) and a
    pgrep sweep (covers watchers started before this field existed or by hand).
    Every candidate is verified by cmdline before signalling, so a recycled pid
    can never be killed, and the serve process itself is never a candidate (its
    cmdline says ``serve``, not ``watch``).
    """
    candidates: set[int] = set()
    try:
        prev = json.loads(Path(state_file).read_text())
        recorded = prev.get("watcher_pid")
        if isinstance(recorded, int) and recorded > 1:
            candidates.add(recorded)
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(
            ["pgrep", "-f", WATCHER_MARKER], capture_output=True, text=True, timeout=5
        )
        candidates.update(int(p) for p in out.stdout.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    me = os.getpid()
    killed: list[int] = []
    for pid in sorted(candidates):
        if pid <= 1 or pid == me:
            continue
        if WATCHER_MARKER not in _pid_cmdline(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while killed and time.monotonic() < deadline:
        if not any(_pid_alive(p) for p in killed):
            break
        time.sleep(0.1)
    for pid in killed:  # grace over: survivors get SIGKILL
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return killed


def handle_trigger(params: dict, state_file: str | Path) -> tuple[int, dict]:
    """Arm a draft: validate, kill any running watcher, write trigger.json and
    reset the state file to a fresh 'armed' state -- all before the reply, so the
    page switches to the new draft the moment Arm is clicked (the runner only
    consumes the trigger on its next tick, up to 60s later).

    ``params`` maps form names to single strings; returns (http_code, payload).
    """
    state_file = Path(state_file)
    league = str(params.get("league") or "").strip()
    seat = str(params.get("seat") or "1")
    interval = str(params.get("interval") or "15")
    start = str(params.get("start") or "").strip()
    if not league or not re.fullmatch(r"\d+\.l\.\d+", league):
        return 400, {
            "ok": False,
            "error": "league must look like 478.l.2638432 (the page adds the 478.l. prefix itself)",
        }
    if not seat.isdigit() or not 1 <= int(seat) <= 12:
        return 400, {"ok": False, "error": "seat must be 1-12"}
    if not interval.isdigit() or not 5 <= int(interval) <= 120:
        return 400, {"ok": False, "error": "interval must be 5-120 seconds"}
    if start and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", start[:16]):
        return 400, {"ok": False, "error": "start must be UTC YYYY-MM-DDTHH:MM"}

    killed = kill_watchers(state_file)
    trigger_path = state_file.parent / "trigger.json"
    tmp = Path(str(trigger_path) + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "league": league,
                "seat": int(seat),
                "interval": int(interval),
                "start_utc": start[:16],
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=1,
        )
    )
    tmp.replace(trigger_path)
    reset_state(
        state_file, league, int(seat), int(interval), start_utc=start[:16] if start else None
    )
    ensure_watch_page(state_file)
    return 200, {"ok": True, "trigger": str(trigger_path), "killed_watchers": killed}


# --- watch page provisioning --------------------------------------------------------


def ensure_watch_page(state_file: str | Path) -> str | None:
    """Copy the packaged watch page into the watch dir when it has none.

    The page is a repo asset (fantasy_gm/draft/watch_page.html); the watch dir is
    a deployable scratch location. Without this, a fresh --dir serves 404 on
    draft night. Never overwrites an existing page. Returns the path or None.
    """
    page = Path(state_file).parent / "index.html"
    if page.exists():
        return None
    src = importlib.resources.files("fantasy_gm.draft").joinpath("watch_page.html")
    page.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(page)


# --- watch mode -------------------------------------------------------------------


def log_rank_deltas(old_gm: dict, new_gm: dict, state: dict, min_move: int = 5) -> None:
    """After a background rebuild, log pool players whose rank moved ≥ ``min_move``.

    This is the rank-delta record task 9.7 promises: the board may move under the
    clock, but every material move is announced on the page's event log rather than
    happening silently.
    """
    old_rank = {r.player_id: r.rank for r in old_gm["board"].rows}
    moves = []
    for r in new_gm["board"].rows:
        o = old_rank.get(r.player_id)
        if o is not None and abs(o - r.rank) >= min_move:
            moves.append((abs(o - r.rank), r.player_name, o, r.rank))
    if not moves:
        return
    moves.sort(reverse=True)
    for _, name, o, n in moves[:8]:
        log_line(state, f"board moved: {name} r{o} -> r{n}")
    if len(moves) > 8:
        log_line(state, f"board moved: +{len(moves) - 8} more")


def run_watch(
    league: str,
    seat: int,
    interval: float,
    state_path: Path,
    serve_port: int | None,
    stop_file: Path,
    punt_budget: float = 8.0,
    news_interval: float = 180.0,
) -> int:
    # Detach from the spawner's process group FIRST: launchd kills a job's whole
    # process group when the job exits (no AbandonProcessGroup), which silently
    # killed every nohup-spawned watcher before its first write (2026-10-08 --
    # arming a new draft then showed the previous draft forever). setsid moves us
    # to our own session, immune to that kill no matter who spawned us.
    try:
        os.setsid()
    except OSError:
        pass  # already a session leader (e.g. run interactively); nothing to detach
    from fantasy_gm.config import Config
    from fantasy_gm.data.store import Store
    from fantasy_gm.draft.live import (
        DraftState,
        build_gm,
        candidate_json,
        column_spec_json,
        poll_draft_results,
        recommend,
        reconcile,
    )
    from fantasy_gm.draft.panels import (
        PuntPanelCache,
        compute_differential,
        differential_json,
        parse_punt,
        punt_panel_json,
    )

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = new_state(league, seat, interval, started)
    state["watcher_pid"] = os.getpid()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Snapshot the previous run's state before this run's first write overwrites it,
    # so the resume below sees the recorded draft rather than the fresh empty state.
    try:
        prev = load_state(state_path)
    except FileNotFoundError:
        prev = None
    write_state(state_path, state)
    page_written = ensure_watch_page(state_path)
    if page_written:
        print(f"wrote watch page: {page_written}", flush=True)

    def http_state_server() -> None:
        """Answer the page's state.json straight from memory (localhost only)."""

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path.split("?")[0] == "/state.json":
                    body = json.dumps(state, indent=1).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def log_message(self, *a) -> None:  # silence per-request stderr
                pass

        ThreadingHTTPServer(("127.0.0.1", serve_port), H).serve_forever()

    if serve_port:
        threading.Thread(target=http_state_server, daemon=True).start()

    # as_of is the BUILD DATE (task 9.7): the hard-coded 2025-10-20 made every
    # 2026-dated designation invisible at draft time (verified: Haliburton rendered r5
    # uncapped instead of r42 under the QUESTIONABLE cap). A rebuild costs ~2s — spent
    # here and in the background thread, never on the clock.
    as_of = time.strftime("%Y-%m-%d", time.gmtime())
    print(f"Building board + market order (once) ... (as_of {as_of})", flush=True)
    store = Store(Config().db_path)
    from fantasy_gm.projections import news as news_mod

    gm = build_gm(store, "2025-26", as_of)
    # The news refresher swaps this box under the lock; the poll loop snapshots it once
    # per iteration, so a mid-poll swap can never mix two boards' parts.
    gm_box = [gm]
    gm_lock = threading.Lock()
    pool = gm["pool"]
    names = gm["names"]
    basis = gm["basis"]  # the panels read the fitted basis; nothing re-measures per pick
    punt_cache = PuntPanelCache()
    punt_custom: tuple[str, ...] = ()
    # Full-universe directory, not the board-only names map: platform picks arrive with
    # Yahoo game ids and foreign-id names must resolve through the whole directory.
    dir_by_id, dir_by_key = gm["directory"]
    draft_state = DraftState(league_key=league, n_teams=12, my_seat=seat)
    # Re-arm on the same league+seat resumes the recorded draft (crash recovery):
    # picks already in the state file are adopted so nothing is lost or re-logged.
    if prev and prev.get("league") == league and prev.get("seat") == seat and prev.get("picks"):
        from fantasy_gm.draft.live import Pick

        # Adopt only picks already in our id space; raw platform ids from an older run
        # (before the id translation existed) are re-fed and mapped by the platform
        # poll, so dropping them here loses nothing.
        adopted = [pk for pk in prev["picks"] if pk["player_id"] in dir_by_id]
        dropped = len(prev["picks"]) - len(adopted)
        draft_state.picks = [
            Pick(
                number=pk["number"],
                player_id=pk["player_id"],
                team_seat=pk["seat"],
                source="live",
                name=pk.get("name", ""),
            )
            for pk in adopted
        ]
        # The page reads the state FILE, and the next full picks write only happens
        # after a successful platform poll — restore the visible record now, or a
        # crash-restart shows an empty draft until the league answers again
        # (hit for real restarting the finished mock's watcher, 2026-10-17).
        state["picks"] = prev["picks"]
        state["pick_count"] = len(draft_state.picks)
        log_line(
            state,
            f"resumed {len(draft_state.picks)} picks from previous run"
            + (
                f"; dropped {dropped} untranslatable platform ids (platform re-feeds them)"
                if dropped
                else ""
            ),
        )
    state["status"] = "watching"
    state["board_players"] = len(pool)
    log_line(
        state, f"board ready: {len(pool)} players; watching seat {seat} of {league} (as_of {as_of})"
    )
    write_state(state_path, state)

    # --- background news refresher (task 9.7) ------------------------------------

    # NBA wire visibility: log its shape (row count) only when it changes — one
    # heartbeat at arm, silence while identical, a line when the league's rosters move.
    nba_heartbeat = {"rows": None}

    def news_refresh_cycle() -> None:
        """One pass: ingest the no-token wires, refresh the pricing authority when the
        token allows, and rebuild + atomically swap the board if anything moved.

        Opens its OWN store connection and closes it at the end: sqlite3 connections
        are single-thread by default, so a connection made in the main thread would
        raise the moment this thread touched it (caught live, 2026-10-15).
        """
        store2 = Store(Config().db_path)
        try:
            _news_refresh_cycle_body(store2)
        finally:
            store2.close()

    def _news_refresh_cycle_body(store2) -> None:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        changed = False
        try:
            rows = news_mod.fetch_yahoo_injuries_page(
                save_path="data/raw_cache/yahoo_injuries_page.html"
            )
            r = news_mod.ingest_yahoo_injuries(store2, rows, today, today=today)
            if r.stored_designations or r.stored_events:
                changed = True
                extra = (
                    f"; {len(r.unresolved)} unresolved, {len(r.unmapped)} unmapped"
                    if r.unresolved or r.unmapped
                    else ""
                )
                log_line(
                    state,
                    f"yahoo-web injuries: +{r.stored_designations} status, "
                    f"+{r.stored_events} event(s){extra}",
                )
        except Exception as exc:  # noqa: BLE001 - a dead wire is visible, never fatal
            log_line(state, f"yahoo-web injuries wire failed: {exc}")
        try:
            items = news_mod.fetch_yahoo_rss(save_path="data/raw_cache/yahoo_news_rss.xml")
            r = news_mod.ingest_yahoo_news(store2, items, today)
            if r.stored_events:
                changed = True
                log_line(state, f"yahoo news RSS: +{r.stored_events} item(s)")
        except Exception as exc:  # noqa: BLE001
            log_line(state, f"yahoo news RSS wire failed: {exc}")
        try:
            from fantasy_gm.config import FORWARD_SEASON
            from fantasy_gm.data.cache import RawCache

            new_events, rows, folded = nba_roster_moves(
                store2, today, RawCache(Config().cache_dir), FORWARD_SEASON
            )
            if new_events or folded:
                changed = True  # a fold re-prices: the board must rebuild on it (9.11)
            suffix = f", {folded} roster row(s) re-priced" if folded else ""
            if nba_heartbeat["rows"] != rows:  # shape change (or first cycle): say so
                nba_heartbeat["rows"] = rows
                log_line(
                    state,
                    f"nba playerindex wire: {rows} rows, {new_events} new move(s){suffix}",
                )
            elif new_events or folded:
                log_line(state, f"nba playerindex wire: +{new_events} move(s){suffix}")
        except Exception as exc:  # noqa: BLE001 - a dead wire is visible, never fatal
            nba_heartbeat["rows"] = None
            log_line(state, f"nba playerindex wire failed: {exc}")
        try:
            lines, yahoo_changed = yahoo_credentialed_refresh(store2, league, today)
            for line in lines:
                log_line(state, line)
            if yahoo_changed:
                changed = True
        except FileNotFoundError:
            log_line(state, "yahoo token missing — credentialed refresh off (wires still live)")
        except Exception as exc:  # noqa: BLE001 - 401 etc: degrade loudly, keep the board
            log_line(state, f"yahoo credentialed refresh unavailable: {exc} — wires still live")
        try:
            from fantasy_gm.config import FORWARD_SEASON
            from fantasy_gm.data.cache import RawCache
            from fantasy_gm.data.nba_source import refresh_schedule

            in_window, sched_changed, window_end = refresh_schedule(
                store2, RawCache(Config().cache_dir), FORWARD_SEASON, today
            )
            state["schedule_window_end"] = window_end
            if sched_changed:
                changed = True  # remaining-games math reads the games table
                log_line(
                    state,
                    f"schedule refresh: {in_window} games in window through {window_end}",
                )
        except Exception as exc:  # noqa: BLE001 - a dead wire is visible, never fatal
            log_line(state, f"espn schedule wire failed: {exc}")
        state["news_sources"] = news_mod.source_age(store2, today)
        if not changed:
            return
        try:
            new_gm = build_gm(store2, "2025-26", today)
        except Exception as exc:  # noqa: BLE001
            log_line(state, f"board rebuild failed: {exc} — the previous board stands")
            return
        with gm_lock:
            old_gm, gm_box[0] = gm_box[0], new_gm
        log_rank_deltas(old_gm, new_gm, state)

    def refresher() -> None:
        # First cycle immediately: the wires feed the flags without waiting an interval.
        try:
            news_refresh_cycle()
        except Exception as exc:  # noqa: BLE001
            log_line(state, f"news refresh error: {exc}")
        write_state(state_path, state)
        while not stop_file.exists():
            time.sleep(news_interval)
            if stop_file.exists():
                return
            try:
                news_refresh_cycle()
            except Exception as exc:  # noqa: BLE001
                log_line(state, f"news refresh error: {exc}")
                write_state(state_path, state)

    threading.Thread(target=refresher, name="news-refresher", daemon=True).start()

    seen = len(draft_state.picks)  # adopted picks are history, not fresh log lines
    placeholder_logged = False
    last_rec_printed_picks = -1
    last_punt_key: tuple[int, tuple[str, ...]] | None = None
    last_differential_picks = -1
    while True:
        if stop_file.exists():
            log_line(state, "stop requested; watcher exits (page goes stale on purpose)")
            state["status"] = "stopped"
            state["watcher_pid"] = None
            write_state(state_path, state)
            stop_file.unlink()
            return 0
        state["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # Snapshot the board box under the lock (task 9.7): the refresher may have
        # swapped in a rebuilt board between polls; this poll uses exactly one board.
        with gm_lock:
            gm = gm_box[0]
        pool, names, basis = gm["pool"], gm["names"], gm["basis"]
        # A custom punt request (page POSTs /punt -> serve writes punt.json -> we consume
        # it here, once). An unparseable request is consumed too and logged, never retried.
        punt_req = state_path.parent / "punt.json"
        if punt_req.exists():
            raw_spec = ""
            try:
                raw_spec = str((json.loads(punt_req.read_text()) or {}).get("punt") or "")
            except (OSError, ValueError):
                pass
            punt_req.unlink(missing_ok=True)
            try:
                punt_custom = parse_punt(raw_spec) if raw_spec else ()
                log_line(
                    state,
                    "punt panel set: "
                    + (", ".join(punt_custom) if punt_custom else "balanced (cleared)"),
                )
            except ValueError as exc:
                log_line(state, f"punt request ignored: {exc}")
        try:
            picks = poll_draft_results(league)
            state["error"] = None
        except Exception as exc:  # noqa: BLE001 - the page must show every failure
            msg = f"poll failed: {exc}"
            print(f"[{msg}] -- manual entry remains available", flush=True)
            state["error"] = msg
            log_line(state, msg + " (manual entry remains available)")
            write_state(state_path, state)
            time.sleep(interval)
            continue

        n_placeholders = sum(1 for p in picks if p.get("player_id") is None)
        if n_placeholders and not placeholder_logged:
            placeholder_logged = True
            log_line(
                state,
                f"platform pre-fills {n_placeholders} empty draft-order slots; "
                f"ignored (not errors)",
            )
        issues = reconcile(draft_state, picks, dir_by_id, dir_by_key)
        for i in issues:
            log_line(state, f"! {i}")
            print(f"  ! {i}", flush=True)
        state["issues"] = issues
        if len(draft_state.picks) > seen:
            for p in draft_state.picks[seen:]:
                who = "YOU" if p.team_seat == seat else f"seat {p.team_seat}"
                nm = p.name or dir_by_id.get(p.player_id) or names.get(p.player_id, p.player_id)
                log_line(state, f"#{p.number} {who}: {nm}")
                print(f"#{p.number:>3} {who:<4} {nm}", flush=True)
            seen = len(draft_state.picks)
        state["pick_count"] = len(draft_state.picks)
        state["picks"] = [
            {
                "number": p.number,
                "seat": p.team_seat,
                "player_id": p.player_id,
                "name": p.name or dir_by_id.get(p.player_id) or names.get(p.player_id, p.player_id),
            }
            for p in draft_state.picks
        ]
        # The differential is recomputed when the draft state moved (R6: after every
        # pick). It is display: nothing here touches candidate ordering.
        if len(draft_state.picks) != last_differential_picks:
            state["differential"] = differential_json(compute_differential(draft_state, basis))
            last_differential_picks = len(draft_state.picks)
        # Recs run every poll, not only on our turn: between our picks the card is a
        # preview -- top of the board with survival projected to OUR next pick (the
        # survival math was always relative to my_seat; the card now says so).
        my_next = draft_state.my_next_pick()
        if my_next is not None:
            rec = recommend(
                store,
                "2025-26",
                draft_state,
                pool,
                board=gm["board"],
                adp_order=gm["adp_order"],
                names=names,
                budget_s=8.0,
                top_n=PAGE_TOP_N,
                as_of=as_of,
            )
            # Print the full rec block only when it changed meaningfully: our turn, or a
            # new pick landed since the last print (a per-poll print is near-duplicate
            # noise across 30-90s clocks).
            if draft_state.is_my_pick() or state["pick_count"] != last_rec_printed_picks:
                rendered = (
                    "\n".join(
                        f"  {c.name}  value {c.total:.2f}  vs safe {c.value_over_safe:+.2f}  "
                        f"surv {c.survival:.0%}"
                        for c in rec.candidates
                    )
                    or "  no candidates"
                )
                print(
                    f"Pick {rec.pick_number} -- seat {rec.on_the_clock} on the clock\n{rendered}",
                    flush=True,
                )
                last_rec_printed_picks = state["pick_count"]
            state["recommendation"] = {
                "pick_number": rec.pick_number,
                "on_the_clock": rec.on_the_clock,
                "my_seat": rec.my_seat,
                "mode": rec.mode,
                "degraded": rec.degraded,
                "note": rec.note,
                "for_me": draft_state.is_my_pick(),
                "my_next_pick": my_next,
                # The page renders this table from the same spec the terminal does (4.3):
                # the picker menu plus the additive per-candidate fields (adp/adp_dev stay
                # null when the market never priced the player; neg_cats worst first).
                "column_spec": column_spec_json(),
                "candidates": [candidate_json(c) for c in rec.candidates],
            }
        else:
            state["recommendation"] = None
        # Punt panel (R7/R8): one attempt per (pick, custom set); a pick change or a new
        # custom set invalidates it. When the budget overruns, the cache hands back the
        # last complete panel marked stale — and we do not keep retrying within the pick
        # unless the page still has no complete panel at all.
        punt_key = (draft_state.pick_number, punt_custom)
        prev_punt = state.get("punt_panel")
        if punt_key != last_punt_key or not (prev_punt and prev_punt.get("complete")):
            panel = punt_cache.get(
                draft_state, basis, gm["adp_order"], names, custom=punt_custom,
                budget_s=punt_budget, top_n=PAGE_TOP_N,
            )
            last_punt_key = punt_key
            state["punt_panel"] = punt_panel_json(panel)
        write_state(state_path, state)
        time.sleep(interval)


# --- serve mode -------------------------------------------------------------------


class StateAwareStaticServer:
    """Static file server (Range support) plus /state.json read from disk.

    This is the process the Funnel proxy points at. /state.json reads the watcher's
    state file, so the page keeps working even if this process starts before or after
    the watcher.
    """

    def __init__(self, directory: Path, port: int, state_path: Path) -> None:
        directory = directory.resolve()
        state_file = Path(state_path)

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(directory), **kw)

            def end_headers(self) -> None:  # noqa: N802 - stdlib naming
                # Everything this serves is a live document (page edits, watcher state).
                # Without this, browsers heuristically cache the HTML and keep showing a
                # stale page after an edit -- bit the form validation once (2026-10-07).
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path.split("?")[0] == "/state.json":
                    body = self._state_body()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def _state_body(self) -> bytes:
                try:
                    return json.dumps(load_state(state_file), indent=1).encode()
                except FileNotFoundError:
                    return json.dumps({"status": "waiting"}).encode()

            def _reply(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _do_trigger(self) -> None:
                """Arm via handle_trigger: kill running watchers, write trigger.json
                for the runner launchd job, and reset the state file so the page
                shows the NEW draft immediately (the runner only consumes the
                trigger on its next tick, up to 60s later)."""
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                params = {k: (v or [""])[0] for k, v in q.items()}
                code, payload = handle_trigger(params, state_file)
                self._reply(code, payload)

            def _do_punt(self) -> None:
                """Set the watcher's custom punt set: writes punt.json next to the state
                file; the watcher consumes it on its next poll (same handoff as trigger).
                An empty spec clears back to the named builds only."""
                from fantasy_gm.draft.panels import parse_punt

                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(min(n, 4096)) if n else b""
                try:
                    spec = str((json.loads(body or b"{}") or {}).get("punt") or "")
                except ValueError:
                    spec = ""
                try:
                    cats = parse_punt(spec) if spec else []
                except ValueError as exc:
                    self._reply(400, {"ok": False, "error": str(exc)})
                    return
                path = Path(state_file).parent / "punt.json"
                tmp = Path(str(path) + ".tmp")
                tmp.write_text(
                    json.dumps({"punt": spec, "created": time.strftime("%H:%M:%S", time.gmtime())})
                )
                tmp.replace(path)
                self._reply(200, {"ok": True, "punt": list(cats)})

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                """Only /trigger (arm the runner), /punt (custom punt set) and /stop
                (sentinel file). Nothing else."""
                route = self.path.split("?")[0]
                if route == "/trigger":
                    self._do_trigger()
                    return
                if route == "/punt":
                    self._do_punt()
                    return
                if route != "/stop":
                    self.send_error(404)
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                body = self.rfile.read(min(n, 4096)) if n else b""
                try:
                    ok = json.loads(body or b"{}").get("action") == "stop"
                except Exception:
                    ok = False
                if not ok:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"expected {"action":"stop"}"}')
                    return
                Path(str(state_file) + ".stop").write_text("")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a) -> None:  # silence per-request stderr
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), H)

    def serve_forever(self) -> None:
        self._httpd.serve_forever()


def run_serve(directory: str, port: int, state_path: str) -> int:
    StateAwareStaticServer(Path(directory), port, Path(state_path)).serve_forever()
    return 0


# --- cli --------------------------------------------------------------------------

USAGE = __doc__


def _flag(rest: list[str], name: str) -> str | None:
    return rest[rest.index(name) + 1] if name in rest else None


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(USAGE)
        return 1
    mode, rest = argv[0], argv[1:]
    if mode == "serve" and len(rest) >= 3:
        # Positional (dir port state) or named (--dir --port --state) both accepted;
        # the launchd job uses named flags so the plist reads at a glance.
        directory = _flag(rest, "--dir") or (rest[0] if not rest[0].startswith("--") else None)
        port = int(
            _flag(rest, "--port")
            or (rest[1] if len(rest) > 1 and not rest[1].startswith("--") else 0)
        )
        state = _flag(rest, "--state") or (
            rest[2] if len(rest) > 2 and not rest[2].startswith("--") else None
        )
        if not (directory and port and state):
            print(USAGE)
            return 1
        run_serve(directory, port, state)
        return 0
    if mode == "watch" and rest:
        league = rest[0]
        seat = int(rest[rest.index("--seat") + 1]) if "--seat" in rest else 1
        interval = float(rest[rest.index("--interval") + 1]) if "--interval" in rest else 15.0
        state_path = (
            Path(rest[rest.index("--out") + 1])
            if "--out" in rest
            else Path("data/draft_watch_state.json")
        )
        serve_port = int(rest[rest.index("--serve-port") + 1]) if "--serve-port" in rest else None
        stop_file = (
            Path(rest[rest.index("--stop-file") + 1])
            if "--stop-file" in rest
            else Path(str(state_path) + ".stop")
        )
        punt_budget = (
            float(rest[rest.index("--punt-budget") + 1]) if "--punt-budget" in rest else 8.0
        )
        news_interval = (
            float(rest[rest.index("--news-interval") + 1]) if "--news-interval" in rest else 180.0
        )
        return run_watch(
            league,
            seat,
            interval,
            state_path,
            serve_port,
            stop_file,
            punt_budget=punt_budget,
            news_interval=news_interval,
        )
    if mode == "reset-state" and "--out" in rest and "--league" in rest:
        # Runner helper: write a fresh 'armed' state at trigger-consume time so the
        # page never shows a previous draft, even when the trigger didn't come
        # through the page's own /trigger endpoint.
        out = Path(_flag(rest, "--out") or "")
        league = _flag(rest, "--league") or ""
        seat = int(_flag(rest, "--seat") or 1)
        interval = float(_flag(rest, "--interval") or 15)
        start = _flag(rest, "--start")
        reset_state(out, league, seat, interval, start_utc=start or None)
        return 0
    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
