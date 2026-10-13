// DOM-shim smoke test for the watch page's rec rendering (draft-critical page).
// Loads the page's actual <script> in a fresh context with a minimal DOM, then drives
// renderRec both ways: with a spec (new watcher state) and without one (old state file),
// plus picker persistence and click behavior.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'fantasy_gm', 'draft', 'watch_page.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const chipEls = {};
function makeEl(id) {
  const el = {
    id, _handlers: {}, _toggles: [], style: {}, dataset: {},
    value: '', disabled: false, textContent: '', _html: '',
    set className(v) { this._cls = String(v); }, get className() { return this._cls || ''; },
    set innerHTML(v) {
      this._html = String(v);
      this._keys = [...this._html.matchAll(/data-key="([^"]+)"/g)].map(m => m[1]);
      this._cells = [...this._html.matchAll(/<(th|td)(?:\s+class="([^"]*)")?>(.*?)<\/\1>/g)]
        .map(m => ({ tag: m[1], cls: m[2] || '', text: m[3] }));
      // A real DOM destroys removed children along with their handlers and dataset;
      // model that for the picker host only, so stale chip objects from a previous
      // picker build cannot be clicked after a rebuild.
      if (this.id === 'rec-picker') for (const k of Object.keys(chipEls)) delete chipEls[k];
    },
    get innerHTML() { return this._html; },
    addEventListener(t, fn) { (this._handlers[t] ||= []).push(fn); },
    click() { (this._handlers.click || []).forEach(fn => fn()); },
    querySelectorAll(sel) {
      if (sel === '.chip') {
        return this._keys.map(k => {
          const e = (chipEls[k] ||= makeEl('chip:' + k));
          e.dataset.key = k;
          return e;
        });
      }
      return [];
    },
    classList: { _t: [], toggle(cls, force) { this._t.push([cls, force]); },
                 last() { return this._t[this._t.length - 1]; } },
  };
  return el;
}
const els = {};
const $ = id => (els[id] ||= makeEl(id));
const store = {};
const sandbox = {
  document: {
    getElementById: $,
    createElement: () => ({ set textContent(v) { this._t = v; }, get innerHTML() { return this._t; } }),
  },
  localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  },
  location: { pathname: '/x/' },
  fetch: async () => ({ ok: false }),
  setInterval: () => 0, clearInterval: () => {},
  console, Date, JSON, Math, Number, Object, Array, String, isNaN, setTimeout: () => {},
};
sandbox.window = sandbox;
let result = null;
sandbox.__report = r => { result = r; };

const SPEC = [
  { key: 'rk', header: 'rk', default: true }, { key: 'player', header: 'player', default: true },
  { key: 'value', header: 'value', default: true }, { key: 'vs_safe', header: 'vs safe', default: true },
  { key: 'surv', header: 'surv', default: true }, { key: 'adp', header: 'adp', default: true },
  { key: 'adp_dev', header: 'adp\u2212rk', default: false },
  { key: 'top_cats', header: 'top cats', default: true },
  { key: 'neg_cats', header: 'neg cats', default: true },
];
const NEW_REC = {
  pick_number: 5, on_the_clock: 1, my_seat: 1, mode: 'static', degraded: false, note: '',
  for_me: true, my_next_pick: 5, column_spec: SPEC,
  candidates: [{
    name: 'Shai Gilgeous-Alexander', board_rank: 1, total: 12.34, value_over_safe: 2.5,
    survival: 0.512, categories: { pts: 3.2, tov: -1.1, ast: 2.0 }, engine_value: null,
    adp: 2, adp_dev: 1, neg_cats: [['tov', -1.1]],
  }, {
    name: 'Unpriced Guy', board_rank: 7, total: 9.1, value_over_safe: 0.4,
    survival: 0.8, categories: { reb: 1.5 }, engine_value: null,
    adp: null, adp_dev: null, neg_cats: [],
  }],
};
const OLD_REC = JSON.parse(JSON.stringify(NEW_REC)); delete OLD_REC.column_spec;

vm.runInNewContext(script + `
;(function () {
  const out = {};
  renderRec(__NEW_REC);
  out.new_table = $('rec-body').innerHTML;
  out.new_picker = $('rec-picker').innerHTML;
  out.new_picker_display = $('rec-picker').style.display;
  out.toggles = __chipToggles();   // snapshot now: later renders clear the picker DOM
  renderRec(__OLD_REC);
  out.old_table = $('rec-body').innerHTML;
  out.old_picker_display = $('rec-picker').style.display;
  renderRec(null);
  out.null_class = $('rec-body').className;
  out.null_picker_display = $('rec-picker').style.display;
  __report(out);
})();`,
  Object.assign(sandbox, { __NEW_REC: NEW_REC, __OLD_REC: OLD_REC,
                           __chipToggles: () => Object.fromEntries(
                             Object.entries(chipEls).map(([k, el]) => [k, el.classList.last()])) }));const out = result;
const fails = [];
const t = (name, cond) => { if (!cond) fails.push(name + ' :: ' + JSON.stringify(cond)); };
const thsOf = table => [...table.matchAll(/<th>(.*?)<\/th>/g)].map(m => m[1]);

t('new: default keys render in spec order (8 cols, no adp_dev)',
  JSON.stringify(thsOf(out.new_table)) ===
  JSON.stringify(['rk', 'player', 'value', 'vs safe', 'surv', 'adp', 'top cats', 'neg cats']));
t('new: priced adp cell renders 2', out.new_table.includes('<td class="num">2</td>'));
t('new: unpriced adp/adp_dev render dash', out.new_table.includes('—'));
t('new: neg cat labelled, signed, styled',
  out.new_table.includes('TOV -1.1') && out.new_table.includes('cat neg'));
t('new: player cell keeps name class', out.new_table.includes('<td class="name">Shai Gilgeous-Alexander</td>'));
t('new: picker visible with 9 chips', out.new_picker_display === 'flex' &&
  out.new_picker.split('data-key=').length - 1 === 9);
t('new: 8 default chips toggled on, adp_dev off',
  SPEC.every(s => {
    const tog = out.toggles[s.key];
    if (!tog) { console.log('missing toggle for', s.key, '— have:', Object.keys(out.toggles)); return false; }
    return s.default === (tog[1] === true);
  }));
t('old state: fallback table equals old fixed table with pretty headers',
  JSON.stringify(thsOf(out.old_table)) ===
  JSON.stringify(['rk', 'player', 'value', 'vs safe', 'surv', 'adp', 'top cats', 'neg cats']));
t('old state: picker not flexed', out.old_picker_display !== 'flex');
t('null rec: empty state + picker hidden',
  out.null_class === 'empty' && out.null_picker_display === 'none');

// picker persistence + validation: stored bogus key is dropped, adp_dev honored alone.
store['rec-columns'] = JSON.stringify(['adp_dev', 'not_a_column']);
vm.runInNewContext(`renderRec(__NEW_REC); __report($('rec-body').innerHTML);`, sandbox);
t('picker: bogus key dropped, adp_dev honored',
  JSON.stringify(thsOf(result)) === JSON.stringify(['adp\u2212rk']));
store['rec-columns'] = JSON.stringify([]);
vm.runInNewContext(`renderRec(__NEW_REC); __report($('rec-body').innerHTML);`, sandbox);
t('picker: empty selection falls back to defaults', thsOf(result).length === 8);

// chip click: toggle top_cats off persists, toggle adp_dev on persists, re-render follows.
store['rec-columns'] = JSON.stringify(SPEC.filter(s => s.default).map(s => s.key));
vm.runInNewContext(`renderRec(__NEW_REC); __report('ok');`, sandbox);
chipEls['top_cats'].click();
chipEls['adp_dev'].click();
vm.runInNewContext(`renderRec(__NEW_REC); __report($('rec-body').innerHTML);`, sandbox);
const gotThs = thsOf(result);
const gotStore = (() => { try { return JSON.parse(store['rec-columns']).sort(); } catch (e) { return String(store['rec-columns']); } })();
t('chip click: top_cats off + adp_dev on, persisted and re-rendered',
  JSON.stringify(gotThs) ===
  JSON.stringify(['rk', 'player', 'value', 'vs safe', 'surv', 'adp', 'adp\u2212rk', 'neg cats']) &&
  JSON.stringify(gotStore) ===
  JSON.stringify(['adp', 'adp_dev', 'neg_cats', 'player', 'rk', 'surv', 'value', 'vs_safe']));
if (fails.length) console.log('debug: ths=' + JSON.stringify(gotThs) + ' store=' + JSON.stringify(gotStore));

if (fails.length) { console.log('FAIL:\n  ' + fails.join('\n  ')); process.exit(1); }
console.log('PAGE SMOKE OK — 13 assertions passed');
