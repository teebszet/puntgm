"""Generate the puntgm.com /tools page (site/dist/tools/index.html).

Static shell only — every number arrives from the API (/api/tools/*), which serves
the webtools payloads behind the Yahoo gate. Keeping the page a generated artifact
preserves the site's one rule (nothing published here is hand-placed): the deploy
script runs build_site.py first, then this, so a dist is always reproducible from
the two generators in order.
"""

from __future__ import annotations

import argparse
from pathlib import Path

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>puntgm — tools</title>
<style>
  :root { --ink:#111; --mut:#667; --line:#e3e6ea; --bg:#f7f8fa; --card:#fff;
          --good:#0a7d33; --bad:#b3261e; --warn:#9a6700; --acc:#0b57d0; }
  * { box-sizing: border-box; }
  body { margin:0; font:14px/1.45 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
         color:var(--ink); background:var(--bg); }
  header { display:flex; align-items:center; gap:12px; padding:10px 16px;
           background:var(--card); border-bottom:1px solid var(--line);
           position:sticky; top:0; z-index:2; }
  header h1 { font-size:16px; margin:0 auto 0 0; }
  header .who { color:var(--mut); font-size:12px; }
  button { font:inherit; padding:5px 12px; border-radius:6px; border:1px solid var(--line);
           background:var(--card); cursor:pointer; }
  button.acc { background:var(--acc); border-color:var(--acc); color:#fff; }
  main { max-width:1200px; margin:0 auto; padding:14px 16px 60px; }
  nav.tabs { display:flex; gap:6px; margin:4px 0 14px; }
  nav.tabs button { border-radius:999px; }
  nav.tabs button[aria-selected="true"] { background:var(--ink); color:#fff;
                                          border-color:var(--ink); }
  .meta { color:var(--mut); font-size:12.5px; margin:0 0 8px; }
  table { border-collapse:collapse; width:100%; background:var(--card);
          border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  th,td { padding:6px 9px; border-bottom:1px solid var(--line); text-align:right;
          white-space:nowrap; }
  th { font-size:11.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--mut);
       background:#fbfcfd; position:sticky; top:44px; }
  th.l,td.l { text-align:left; }
  tbody tr:hover td { background:#f2f6ff; }
  b.win { color:var(--ink); text-decoration:underline 2px var(--acc); }
  .chip { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11.5px;
          font-weight:600; }
  .chip.safe { background:#e7f3ea; color:var(--good); }
  .chip.live { background:#eaf0fb; color:var(--acc); }
  .chip.gone { background:#f8eae9; color:var(--bad); }
  .chip.out { background:#f8eae9; color:var(--bad); }
  .chip.il  { background:#fdf1df; color:var(--warn); }
  .chip.q   { background:#fdf8df; color:var(--warn); }
  .bar { position:relative; height:8px; background:#edf0f3; border-radius:4px;
         min-width:90px; }
  .bar i { position:absolute; inset:0 auto 0 0; border-radius:4px; background:var(--acc); }
  .cellcats { display:grid; grid-template-columns:repeat(9,12px); gap:1px; margin-top:2px; }
  .cellcats span { height:10px; border-radius:2px; background:#e6e9ed; }
  .favor { cursor:default; }
  section[hidden] { display:none; }
  .err { color:var(--bad); }
  .foot { color:var(--mut); font-size:12px; margin-top:8px; }
</style>
</head>
<body>
<header>
  <h1>puntgm tools</h1>
  <span class="who" id="who"></span>
  <button id="authbtn" class="acc"></button>
</header>
<main>
  <nav class="tabs" role="tablist">
    <button data-tab="wire" aria-selected="true">Wire lines</button>
    <button data-tab="matchup" aria-selected="false">Matchup</button>
    <button data-tab="grid" aria-selected="false">Fixture grid</button>
  </nav>
  <section id="wire"></section>
  <section id="matchup" hidden></section>
  <section id="grid" hidden></section>
</main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dash = (v) => (v === null || v === undefined || v === '') ? '—' : esc(v);

async function api(path) {
  const r = await fetch(path, { credentials: 'include' });
  if (r.status === 401) throw { auth: true };
  if (!r.ok) throw { message: (await r.json()).error || ('HTTP ' + r.status) };
  return r.json();
}

function meUI() {
  return api('/api/auth/me').then(me => {
    if (me.signed_in) {
      $('who').textContent = 'signed in · ' + me.guid;
      $('authbtn').textContent = 'Sign out';
      $('authbtn').onclick = () => location.href = '/api/auth/logout';
    } else {
      $('who').textContent = '';
      $('authbtn').textContent = 'Sign in with Yahoo';
      $('authbtn').onclick = () => location.href = '/api/auth/login';
    }
    return me.signed_in;
  }).catch(() => false);
}

function deny(el, err) {
  el.innerHTML = err && err.auth
    ? '<p class="meta">Sign in with Yahoo to use the tools.</p>'
    : '<p class="err">Failed to load: ' + esc(err && err.message || 'unknown') + '</p>';
}

/* ---- wire lines ------------------------------------------------------ */
async function loadWire() {
  const el = $('wire');
  try {
    const d = await api('/api/tools/wire-lines');
    const slate = d.slate;
    const rows = d.rows.map(r => `<tr>
      <td class="l">${esc(r.name)}</td><td>${esc(r.nba_team)}</td>
      <td>${r.out ? '<span class="chip out">OUT</span>' : dash(r.games_left)}</td>
      <td>${dash(r.minutes)}</td>
      ${['pts','reb','ast','stl','blk','fg3m','tov'].map(c =>
        `<td>${r.beat.includes(c) ? '<b class="win">' + esc(Math.round(r.stats[c])) + '</b>'
                                  : esc(Math.round(r.stats[c]))}</td>`).join('')}
      <td>${dash(r.fg_pct)}</td><td>${dash(r.ft_pct)}</td>
    </tr>`).join('');
    const near = d.near_misses.map(r => `<tr>
      <td class="l">${esc(r.name)}</td><td>${esc(r.nba_team)}</td>
      <td class="l">${esc(r.at.join(', '))}</td>
      <td>${dash(r.minutes)}</td>
      <td>${esc(Math.round(r.stats.pts))}</td><td>${esc(Math.round(r.stats.reb))}</td>
      <td>${esc(Math.round(r.stats.ast))}</td><td>${esc(Math.round(r.stats.stl))}</td>
      <td>${esc(Math.round(r.stats.blk))}</td>
    </tr>`).join('');
    el.innerHTML = `
      <p class="meta">Games of ${esc(d.game_date)} · slate ${slate.games} games,
        ${slate.finals} banked · ${esc(String(d.rostered_count))} rostered, wire =
        everyone else · thresholds strictly more than: &gt;20 pts, &gt;5 ast, &gt;9 reb,
        &gt;2 stl, &gt;2 blk · gm left = his NBA team's scheduled games this week
        (week ${esc(d.week ? d.week.start + ' → ' + d.week.end : 'n/a')})</p>
      <table><thead><tr><th class="l">Player</th><th>NBA</th><th>gm left</th><th>min</th>
        <th>pts</th><th>reb</th><th>ast</th><th>stl</th><th>blk</th><th>3pm</th>
        <th>tov</th><th>fg%</th><th>ft%</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="foot">ft% “—” = no free-throw attempts · underlined = beat the threshold</p>
      ${near ? `<p class="meta">Near misses — exactly at a threshold, none beaten:</p>
      <table><thead><tr><th class="l">Player</th><th>NBA</th><th class="l">at</th><th>min</th>
        <th>pts</th><th>reb</th><th>ast</th><th>stl</th><th>blk</th></tr></thead>
        <tbody>${near}</tbody></table>` : ''}`;
  } catch (err) { deny(el, err); }
}

/* ---- matchup --------------------------------------------------------- */
async function loadMatchup() {
  const el = $('matchup');
  try {
    const d = await api('/api/tools/matchup');
    const rows = d.categories.map(r => `<tr>
      <td class="l">${esc(r.category)}</td>
      <td><b>${dash(r.mine)}</b> <span style="color:var(--mut)">±${dash(r.mine_std)}
        [${dash(r.mine_lo)}–${dash(r.mine_hi)}]</span></td>
      <td><b>${dash(r.opp)}</b> <span style="color:var(--mut)">±${dash(r.opp_std)}
        [${dash(r.opp_lo)}–${dash(r.opp_hi)}]</span></td>
      <td><div class="bar"><i style="width:${Math.round((r.win_prob) * 100)}%"></i></div></td>
      <td>${Math.round(r.win_prob * 100)}%</td>
      <td><span class="chip ${esc(r.label)}">${esc(r.label)}</span></td>
    </tr>`).join('');
    el.innerHTML = `
      <p class="meta">${esc(d.team_name)} vs ${esc(d.opponent_name)} · week
        ${esc(d.period_start)} → ${esc(d.period_end)} · games remaining:
        ${dash(d.games_remaining.mine)} vs ${dash(d.games_remaining.opp)}
        (Yahoo-comparable: per-day scheduled starters, bench/IL/OUT never count)</p>
      <table><thead><tr><th class="l">Cat</th><th>Yours</th><th>Theirs</th>
        <th></th><th>Win%</th><th>Read</th></tr></thead><tbody>${rows}</tbody></table>
      <p class="foot">25th–75th percentile bands from actual per-game σ. A band that
        spans the opponent's number is a coin flip, not a projection — the label
        says which categories are decided.</p>`;
  } catch (err) { deny(el, err); }
}

/* ---- fixture grid ---------------------------------------------------- */
async function loadGrid() {
  const el = $('grid');
  try {
    const d = await api('/api/tools/fixture-grid');
    const cats = d.cats;
    const color = f => f == null ? '#e6e9ed'
      : f > 1.10 ? '#0a7d33' : f > 1.03 ? '#7fbf8f'
      : f < 0.90 ? '#b3261e' : f < 0.97 ? '#e2938e' : '#d9dde2';
    const head = `<tr><th class="l">Player</th><th></th><th class="l">Slot</th>` +
      d.dates.map(dt => `<th>${esc(dt.slice(5))}</th>`).join('') + '</tr>';
    const rows = d.players.map(p => `<tr>
      <td class="l">${esc(p.name)} ${p.status
        ? `<span class="chip ${esc(p.status.toLowerCase())}">${esc(p.status)}</span>` : ''}</td>
      <td>${esc(p.nba_team || '')}</td><td class="l">${esc(p.slot)}</td>
      ${d.dates.map(dt => {
        const g = (p.games || []).find(x => x.date === dt);
        if (!g) return '<td style="color:var(--mut)">·</td>';
        const chips = cats.map(c => {
          const f = g.favorability[c];
          const tip = f ? `${c}: ×${f.favor} (rank #${f.rank ?? '?'}, ${f.basis})` : c;
          return `<span class="favor" title="${esc(tip)}" style="background:${color(f && f.favor)}"></span>`;
        }).join('');
        return `<td class="l"><span style="font-size:12px">${esc(g.is_home ? 'vs ' : '@ ')}${esc(g.opponent)}</span>
          <div class="cellcats">${chips}</div></td>`;
      }).join('')}
    </tr>`).join('');
    const legend = `<span style="display:inline-block;width:10px;height:10px;background:#0a7d33;border-radius:2px"></span> good
      &nbsp;<span style="display:inline-block;width:10px;height:10px;background:#b3261e;border-radius:2px"></span> bad —
      favorability ×league average allowed at that player's position (hover a cell for the number)`;
    el.innerHTML = `
      <p class="meta">Your roster × next ${d.days} days, per-game opponent favorability per
        category. ${legend}</p>
      <div style="overflow:auto; max-height:70vh">
      <table><thead>${head}</thead><tbody>${rows}</tbody></table></div>
      <p class="foot">Basis: ${d.team_games} team-games of 2026-27 box scores
        (${esc(JSON.stringify(d.team_basis || {})).slice(0, 0)}rate cats volume-weighted;
        thin samples fall back to last season).</p>`;
  } catch (err) { deny(el, err); }
}

/* ---- tabs ------------------------------------------------------------ */
const loaders = { wire: loadWire, matchup: loadMatchup, grid: loadGrid };
document.querySelectorAll('nav.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav.tabs button').forEach(x =>
    x.setAttribute('aria-selected', x === b));
  document.querySelectorAll('main section').forEach(s => s.hidden = s.id !== b.dataset.tab);
  loaders[b.dataset.tab]();
});

meUI().then(signed => { if (signed) loadWire(); });
</script>
</body>
</html>
"""


def build_tools(out: Path) -> Path:
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    page = tools / "index.html"
    page.write_text(PAGE)
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="site/dist", help="static output dir (site/dist)")
    args = ap.parse_args()
    page = build_tools(Path(args.out))
    print(f"wrote {page} ({page.stat().st_size / 1024:.0f}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
