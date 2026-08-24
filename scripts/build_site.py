"""Generate the static site published at puntgm.com — the free board and the evidence for it.

Everything on the site comes from one `build_board` call per punt build, the same function the
replay harness grades. That is deliberate: the GTM claim is "the track record is the content",
which is only true if the artifact and the thing that was measured are the same object.

The output is a plain directory of files. No framework, no backend, no build step beyond this
script — the board is 156 rows across 9 builds, which fits in one JSON payload and sorts client
side. Run::

    FANTASY_GM_DATA_DIR=... python scripts/build_site.py --as-of 2026-08-24 --out site/dist

**What the board may and may not claim.** Per-game production is *measured* from the last
completed season's game logs. It is not projected forward — a category-level forward projection
is Track B, and A-DRAFT-5, the gate deciding whether that model beats naive carry-forward, is
unrun. The only forward model on the page is expected games. Every page carries the board's own
`basis` line saying exactly that, and `render_board_page` cannot drop it.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path

from fantasy_gm.config import Config
from fantasy_gm.data.store import Store
from fantasy_gm.draft.board import PUNT_BUILDS, board_json, build_board

# The season whose game logs supply per-game production, and the season being drafted. They are
# necessarily different: a preseason board exists before a single game of its own season.
PRODUCTION_SEASON = "2025-26"
DRAFT_SEASON = "2026-27"

CAT_LABELS = {
    "pts": "PTS", "reb": "REB", "ast": "AST", "stl": "STL", "blk": "BLK",
    "fg3m": "3PM", "fg_pct": "FG%", "ft_pct": "FT%", "tov": "TO",
}


def build_all(store, as_of: str, pool_size: int) -> dict:
    """Every punt build, in one payload the page can switch between without a round trip."""
    builds = []
    for name in PUNT_BUILDS:
        board = build_board(
            store, PRODUCTION_SEASON, build=name, as_of=as_of, pool_size=pool_size
        )
        payload = board_json(board)
        for row in payload["rows"]:
            row["g_score"] = round(row["g_score"], 3)
            row["expected_games"] = (
                round(row["expected_games"], 1) if row["expected_games"] is not None else None
            )
            row["availability_rate"] = (
                round(row["availability_rate"], 4)
                if row["availability_rate"] is not None
                else None
            )
            row["categories"] = {c: round(v, 2) for c, v in row["categories"].items()}
        builds.append(payload)
    return {
        "production_season": PRODUCTION_SEASON,
        "draft_season": DRAFT_SEASON,
        "as_of": as_of,
        "generated": date.today().isoformat(),
        "builds": builds,
    }


# --- rendering ---------------------------------------------------------------

STYLE = """
:root {
  --bg: #0d1117; --panel: #151b23; --line: #232c38; --ink: #e6edf3;
  --dim: #8b98a8; --accent: #4cc38a; --warn: #e0a458; --link: #6cb6ff;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
a { color: var(--link); }
.wrap { max-width: 1080px; margin: 0 auto; padding: 0 20px 80px; }
header { border-bottom: 1px solid var(--line); margin-bottom: 32px; }
header .wrap { display: flex; align-items: baseline; gap: 28px; padding: 18px 20px; }
.brand { font-weight: 700; font-size: 19px; letter-spacing: -0.02em; }
.brand span { color: var(--accent); }
nav a { margin-right: 18px; text-decoration: none; color: var(--dim); font-size: 14px; }
nav a:hover, nav a.on { color: var(--ink); }
h1 { font-size: 30px; line-height: 1.25; letter-spacing: -0.02em; margin: 0 0 14px; }
h2 { font-size: 20px; margin: 40px 0 12px; letter-spacing: -0.01em; }
h3 { font-size: 16px; margin: 28px 0 8px; }
p, li { color: #c9d4e0; }
.lede { font-size: 17px; color: var(--ink); max-width: 68ch; }
.dim { color: var(--dim); }
.basis {
  background: var(--panel); border: 1px solid var(--line); border-left: 3px solid var(--warn);
  padding: 12px 16px; font-size: 13px; color: var(--dim); margin: 22px 0; border-radius: 4px;
}
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 24px 0 14px; }
select, input {
  background: var(--panel); color: var(--ink); border: 1px solid var(--line);
  padding: 7px 10px; border-radius: 5px; font-size: 14px;
}
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 7px 9px; text-align: right; border-bottom: 1px solid var(--line); }
th { font-size: 12px; color: var(--dim); font-weight: 600; text-transform: uppercase;
     letter-spacing: 0.04em; cursor: pointer; user-select: none; white-space: nowrap; }
th:hover { color: var(--ink); }
th.l, td.l { text-align: left; }
tbody tr:hover { background: #131a22; }
td.name { font-weight: 500; }
.up { color: var(--accent); }
.down { color: #e06c75; }
.muted { color: var(--dim); }
.cat { font-size: 12px; color: var(--dim); }
table.small { font-size: 13px; }
table.small th, table.small td { padding: 6px 8px; }
code { background: var(--panel); padding: 1px 5px; border-radius: 3px; font-size: 13px; }
footer { border-top: 1px solid var(--line); margin-top: 60px; padding-top: 20px;
         color: var(--dim); font-size: 13px; }
"""


def _shell(title: str, active: str, body: str, script: str = "") -> str:
    nav = "".join(
        f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>'
        for key, href, label in (
            ("board", "/", "Board"),
            ("evidence", "/evidence.html", "Evidence"),
            ("method", "/method.html", "Method"),
        )
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{STYLE}</style>
</head><body>
<header><div class="wrap">
  <div class="brand">punt<span>gm</span></div>
  <nav>{nav}</nav>
</div></header>
<div class="wrap">
{body}
<footer>
  Built from real NBA box scores. Every number on this site is reproducible from the
  <a href="https://github.com/teebszet/puntgm">open-source repository</a> that generated it.
</footer>
</div>
{f"<script>{script}</script>" if script else ""}
</body></html>
"""


def render_board_page(data: dict) -> str:
    basis = data["builds"][0]["basis"]
    options = "".join(
        f'<option value="{b["build"]}">{b["label"]}</option>' for b in data["builds"]
    )
    body = f"""
<h1>The rankings you draft from can't see who plays.</h1>
<p class="lede">
  Every free ranking list is a <strong>per-game</strong> number. A per-game number rates a player
  who appears 55 times exactly like one who appears 80. Correcting that is worth
  <strong>+11 to +20 percentage points</strong> of category win rate, measured on three completed
  seasons &mdash; <a href="/evidence.html">here is the measurement</a>.
</p>
<p class="lede dim">
  So this board carries an expected-games column, and a <code>vs&nbsp;z</code> column showing where
  it disagrees with the per-game rankings. That gap is the whole product.
</p>

<div class="controls">
  <select id="build">{options}</select>
  <input id="q" type="search" placeholder="filter players&hellip;" size="22">
  <span class="dim" id="count"></span>
</div>

<table id="board">
  <thead><tr>
    <th class="l" data-k="rank">#</th>
    <th class="l" data-k="player_name">Player</th>
    <th data-k="g_score">Value</th>
    <th data-k="expected_games">Exp. games</th>
    <th data-k="z_rank">Per-game rank</th>
    <th data-k="z_delta">vs z</th>
    <th class="l">Top categories</th>
  </tr></thead>
  <tbody></tbody>
</table>

<div class="basis">{basis}</div>
"""
    script = """
const CATS = __CAT_LABELS__;
let DATA = null, sortKey = 'rank', sortDir = 1;

function currentBuild() {
  const name = document.getElementById('build').value;
  return DATA.builds.find(b => b.build === name);
}
function topCats(row) {
  return Object.entries(row.categories)
    .sort((a, b) => b[1] - a[1]).slice(0, 3)
    .filter(([, v]) => v > 0)
    .map(([c, v]) => CATS[c] + ' +' + v.toFixed(1)).join('  ');
}
function render() {
  if (!DATA) return;
  const b = currentBuild();
  const q = document.getElementById('q').value.toLowerCase();
  let rows = b.rows.filter(r => r.player_name.toLowerCase().includes(q));
  rows = rows.slice().sort((x, y) => {
    const a = x[sortKey], c = y[sortKey];
    if (typeof a === 'string') return sortDir * a.localeCompare(c);
    return sortDir * ((a ?? 0) - (c ?? 0));
  });
  document.querySelector('#board tbody').innerHTML = rows.map(r => {
    const d = r.z_delta ?? 0;
    const cls = d > 0 ? 'up' : d < 0 ? 'down' : 'muted';
    const sign = d > 0 ? '+' : '';
    return `<tr>
      <td class="l muted">${r.rank}</td>
      <td class="l name">${r.player_name}</td>
      <td>${r.g_score.toFixed(2)}</td>
      <td>${r.expected_games == null ? '&mdash;' : r.expected_games.toFixed(1)}</td>
      <td class="muted">${r.z_rank ?? '&mdash;'}</td>
      <td class="${cls}">${sign}${d}</td>
      <td class="l cat">${topCats(r)}</td>
    </tr>`;
  }).join('');
  document.getElementById('count').textContent = rows.length + ' players';
}
document.querySelectorAll('#board th[data-k]').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    // Rank and the per-game rank read best ascending; everything else best descending.
    if (sortKey === k) sortDir *= -1;
    else {
      sortKey = k;
      sortDir = (k === 'rank' || k === 'z_rank' || k === 'player_name') ? 1 : -1;
    }
    render();
  });
});
document.getElementById('build').addEventListener('change', render);
document.getElementById('q').addEventListener('input', render);

// The board is fetched rather than inlined: it is ~390KB and it is regenerated far more often
// than the page around it, so keeping the two apart keeps the committed diff readable.
fetch('data/boards.json')
  .then(r => r.json())
  .then(d => { DATA = d; render(); })
  .catch(() => {
    document.getElementById('count').textContent = 'could not load the board';
  });
""".replace("__CAT_LABELS__", json.dumps(CAT_LABELS))
    return _shell("puntgm — free 9-cat draft board", "board", body, script)


def render_evidence_page(data: dict) -> str:
    body = """
<h1>The measurement</h1>
<p class="lede">
  Every claim below is a replay: draft a team with a strategy, play the season that actually
  happened, count who won more categories. Three completed seasons, twelve draft seats, four
  seeds &mdash; about 29,700 category decisions per arm per seed.
</p>

<h2>Correcting for availability is worth +11 to +20pp</h2>
<p>
  Category win-rate difference, in percentage points, against the per-game z-score that free
  ranking lists publish. Positive means this board won more categories.
</p>
<table class="small"><thead><tr>
  <th class="l">Pair</th><th>2025-26</th><th>2024-25</th><th>2023-24</th>
</tr></thead><tbody>
  <tr><td class="l">null &mdash; the same board in both seats</td>
      <td class="muted">+2.6 &minus;0.7 +2.1 +1.0</td>
      <td class="muted">&minus;0.2 &minus;0.6 &minus;0.2 +0.6</td>
      <td class="muted">+0.1 &minus;0.2 +0.3 &minus;0.5</td></tr>
  <tr><td class="l"><strong>this board vs per-game z-score</strong></td>
      <td class="up">+17.9 +19.6 +17.5 +18.6</td>
      <td class="up">+15.9 +16.1 +15.9 +17.1</td>
      <td class="up">+10.9 +13.1 +12.0 +11.9</td></tr>
  <tr><td class="l">this board vs total-value z-score</td>
      <td class="down">&minus;1.2 &minus;0.9 &minus;1.3 &minus;1.6</td>
      <td class="down">&minus;0.7 &minus;0.7 &minus;1.0 &minus;1.0</td>
      <td class="down">&minus;3.8 &minus;3.8 &minus;3.4 &minus;3.8</td></tr>
</tbody></table>

<p class="dim">
  The first row is a control: the identical board drafted against itself. It is there so you can
  see the noise floor rather than take our word for it. The largest number in it is 2.6pp; the
  smallest in row two is 10.9pp.
</p>

<h2>And the part most people would leave out</h2>
<p>
  Read the third row. Against a <strong>total-value</strong> z-score &mdash; z computed on season
  totals, which Basketball Monster and Hashtag Basketball both expose behind a toggle &mdash;
  this board <strong>loses</strong>, by 0.7 to 3.8pp, in all twelve runs.
</p>
<p>
  That is not a caveat we were forced into. It is the finding. The edge over free rankings is
  <em>availability</em>, not some superior metric, and if you already pay for a tool with a
  total-value mode you already have most of it. What we will not do is quote you the big number
  and hope you never check the small one.
</p>

<h2>Where the number comes from</h2>
<p>
  Each run drafts two strategies head to head in a twelve-team snake, nine ADP-driven bots
  filling the rest, then grades every team against every other team every week of the real
  season. Seats are mirrored: each arm drafts from all twelve positions, and each rotation is run
  again with the draft order reversed.
</p>
<p>
  That mirroring is not decoration. Before we added it, a snake over an odd number of rounds was
  handing the arm listed first up to <strong>+9.5pp</strong> for nothing &mdash; an effect the
  size of the ones we were trying to measure. <a href="/method.html">The full account is here</a>,
  including the other error it hid.
</p>
"""
    return _shell("puntgm — the measurement", "evidence", body)


def render_method_page(data: dict) -> str:
    basis = data["builds"][0]["basis"]
    body = f"""
<h1>Method, and what we got wrong</h1>
<p class="lede">
  Fantasy tools publish rankings. Almost none publish what happens when you draft from them, and
  none we have found publish their own mistakes. Both are here, because a number you cannot check
  is worth nothing.
</p>

<h2>What this board is</h2>
<div class="basis">{basis}</div>
<p>
  In plain terms: <strong>per-game production is measured from last season's box scores.</strong>
  It is not projected forward. The only forward model on this page is expected games &mdash; a
  beta-binomial rate shrunk toward the league pool, so a player with one unlucky season is not
  condemned by it, and a rookie gets the pool rate rather than a silent 82.
</p>
<p>
  We have a forward projection model for category rates. It is not on this page, because the test
  that decides whether it beats simply carrying last season forward has not been run yet. When it
  passes, it ships. If it fails, we will say so here.
</p>

<h2>Two errors we found in our own numbers</h2>

<h3>1. The board was quietly reading the future</h3>
<p>
  Forward boards were built by adding up weekly totals over the weeks a player actually appeared.
  That sounds harmless and is not: it silently keeps each player's realized games <em>per week</em>,
  which is a fact about the season being graded. The board correlated <strong>+0.60</strong> with
  how many games players turned out to play, and <strong>~0.00</strong> with the availability
  projection it had been given.
</p>
<p>
  Fixed by construction rather than by argument. A week is now built as a scheduled number of
  games, each played with a projected probability, so availability can only enter through that
  probability. Correlation afterwards: +0.07.
</p>

<h3>2. Rotating draft seats does not control for draft position</h3>
<p>
  Two strategies seated next to each other rotate together, so they stay adjacent in a fixed
  order, and over an odd number of rounds a snake hands the lower-seated one the earlier pick more
  often. We assumed rotation handled it. Drafting a board <em>against itself</em>, the artifact
  reached <strong>+9.5pp</strong> &mdash; the size of effects we had been reporting.
</p>
<p>
  Every table on this site now carries a null arm: the same strategy in both seats. Without one,
  an artifact and a finding look identical.
</p>

<h2>What we are not claiming</h2>
<ul>
  <li><strong>This is not a variance effect.</strong> The metric behind this board has a
      variance-correction term. We fitted it and the best value was zero &mdash; it was making the
      board worse in every run. The edge is availability.</li>
  <li><strong>It does not beat a paid tool's total-value mode.</strong> It loses to it.
      <a href="/evidence.html">The numbers are on the evidence page.</a></li>
  <li><strong>Draft position is not free.</strong> Our own control shows a single seat's outcome
      swinging wildly even when every drafter runs the identical board.</li>
</ul>

<h2>Reproduce it</h2>
<p>
  The engine, the replay harness and this board are one open-source Python package. The board on
  the front page is the same function call the replay grades &mdash; not a rendering of it, the
  same object. <a href="https://github.com/teebszet/puntgm">github.com/teebszet/puntgm</a>
</p>
"""
    return _shell("puntgm — method", "method", body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--as-of", required=True, help="date the availability projection is made from")
    ap.add_argument("--out", default="site/dist")
    ap.add_argument("--pool-size", type=int, default=156)
    args = ap.parse_args()

    store = Store(Config().db_path)
    data = build_all(store, args.as_of, args.pool_size)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "data").mkdir(parents=True)

    (out / "data" / "boards.json").write_text(json.dumps(data, separators=(",", ":")))
    (out / "index.html").write_text(render_board_page(data))
    (out / "evidence.html").write_text(render_evidence_page(data))
    (out / "method.html").write_text(render_method_page(data))

    total = sum(len(b["rows"]) for b in data["builds"])
    size = (out / "data" / "boards.json").stat().st_size
    print(f"wrote {out}/ — {len(data['builds'])} builds, {total} rows, {size / 1024:.0f}KB JSON")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
