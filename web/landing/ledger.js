// ledger.js — the roommate's paperwork, under the CI badge on the dashboard (plan/roommate/03-interfaces.md §8).
//
//   OUT OF PLACE     one object, in a zone `main` does not have it in (/api/room/ci -> misplaced). Either it is a mess
//                    — the roommate carries it back — or you MEANT it, and that goes through a pull request.
//   CHORES           what the roommate could not put back itself (/api/chores?status=open — roomctl's ledger).
//   PULL REQUESTS    /api/prs — roomctl's own refs. Approving merges into `main`; the room has not moved yet, so the
//                    object shows up above as OWED until a clean fresh pass comes after the move.
//
// A clean room with no chores and no pull requests shows ONE quiet line: `+ pull request` ("move the lamp to the shelf").
// Writes are local, or carry the room's token (03 §8). A phone is not the room's laptop, so the first write from one
// answers 401 and the page asks for the token ONCE: typed by the person, kept in that browser's localStorage, sent as a
// Bearer header. It is never in the page, never in a URL, never logged. No token configured on the server = local only.
const $ = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'text') n.textContent = v; else if (k === 'onclick') n.addEventListener('click', v); else n.setAttribute(k, v === true ? '' : v);
  }
  n.append(...kids.flat().filter((k) => k != null && k !== false));
  return n;
};
const when = (iso) => { const d = new Date(iso); return Number.isNaN(+d) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); };
const VERDICT = {
  mess: 'out of place, and not something it can carry',
  decision: 'moved on purpose? put it back, or open a pull request',
  untracked_shared: 'something new in a shared spot',
};

const KEY = 'gitirl-room-token';
const token = () => { try { return localStorage.getItem(KEY) || ''; } catch { return ''; } };
const keep = (t) => { try { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); } catch { /* private mode: it lasts for this send only */ } };

async function ask(url, init) {
  try {
    const t = init && token();
    const r = await fetch(url, { headers: { accept: 'application/json', ...(init ? { 'content-type': 'application/json' } : {}), ...(t ? { authorization: `Bearer ${t}` } : {}) }, ...init });
    let body = null;
    try { body = await r.json(); } catch { /* an empty body is an answer too */ }
    return { ok: r.ok, status: r.status, body, connected: r.headers.get('x-roommate-backend') !== 'not_connected' };
  } catch (e) { return { ok: false, status: 0, body: { detail: String(e.message || e) } }; }
}

let host = null, busy = false, said = null, retry = null, form = null, canOpen = false;

function mount() {
  if (host) return host;
  const status = document.getElementById('status');
  const after = status && status.querySelector('.changes');
  if (!after) return null;
  host = $('div', { class: 'ledger', hidden: true, 'aria-label': 'Chores and pull requests for the room' });
  after.after(host);
  return host;
}

function object(id) { return $('a', { class: 'oid mono', href: `/object/${encodeURIComponent(id)}`, text: id }); }
function button(text, fn, title) { return $('button', { type: 'button', class: 'ledger-do', title, text, onclick: (e) => { e.currentTarget.textContent = '…'; fn(); } }); }

async function act(label, url, body) {
  if (busy) return;
  busy = true; said = null;
  if (host) host.dataset.busy = label;                          // a merge is a second of git: say it is happening
  const r = await ask(url, { method: 'POST', body: JSON.stringify(body || {}) });
  busy = false;
  if (host) delete host.dataset.busy;
  retry = null;
  if (r.ok) form = null;
  else if (r.status === 401) { if (token()) keep(''); retry = { label, url, body }; said = `${label}: this is not the room’s own laptop, so it needs the room’s token`; }
  else if (r.status === 403) said = `${label}: this server only takes that from the room’s own laptop`;
  else said = `${label}: ${(r.body && r.body.detail) || `the server answered ${r.status}`}`;
  load();
}

// "move the lamp to the shelf": the object, the zone, an optional title. roomctl picks the free spot; the PR row shows it.
async function openForm() {
  form = { objects: [], zones: [], object_id: '', zone: '', title: '' };
  const st = await ask('/api/state?ref=HEAD');
  if (!form) return;
  if (st.ok && st.body) { form.objects = st.body.objects || []; form.zones = Object.keys(st.body.zones || {}); } else said = 'the room’s contents did not load, so there is nothing to choose from';
  load();
}
function formRow() {
  const pick = (name, options, label) => {
    const sel = $('select', { class: 'ledger-in', 'aria-label': label });
    sel.append($('option', { value: '', text: label }), ...options.map(([v, t]) => $('option', { value: v, text: t, selected: form[name] === v })));
    sel.addEventListener('change', () => { form[name] = sel.value; if (name === 'object_id') form.zone = ''; load(); });
    return sel;
  };
  const obj = form.objects.find((o) => o.object_id === form.object_id);
  const title = $('input', { class: 'ledger-in ledger-title-in', type: 'text', maxlength: 120, placeholder: obj && form.zone ? `move ${obj.object_id} to ${form.zone}` : 'a title (optional)', value: form.title, 'aria-label': 'Title' });
  title.addEventListener('input', () => { form.title = title.value; });
  const go = () => act('open a pull request', '/api/prs', { object_id: form.object_id, zone: form.zone, ...(form.title.trim() ? { title: form.title.trim() } : {}) });
  return $('div', { class: 'ledger-row', 'data-kind': 'form' },
    pick('object_id', form.objects.map((o) => [o.object_id, `${o.object_id} · zones/${o.zone}`]), 'move…'),
    pick('zone', form.zones.filter((z) => !obj || z !== obj.zone).map((z) => [z, `to zones/${z}`]), 'to…'),
    title,
    $('span', { class: 'ledger-acts' },
      obj && form.zone && button('open', go, 'open the pull request: nothing moves until it is approved'),
      button('cancel', () => { form = null; said = null; load(); })),
    obj && form.zone && $('span', { class: 'ledger-why ledger-preview', text: `${obj.object_id}: zones/${obj.zone} → zones/${form.zone}. Nothing moves until it is approved; then the roommate picks a free spot there and carries it over.` }));
}
function tokenRow() {
  const inp = $('input', { class: 'ledger-in', type: 'password', autocomplete: 'off', placeholder: 'the room’s token', 'aria-label': 'The room’s token' });
  const use = () => { const t = inp.value.trim(); if (!t || !retry) return; keep(t); const r = retry; retry = null; act(r.label, r.url, r.body); };
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') use(); });
  return $('div', { class: 'ledger-row', 'data-kind': 'token' }, inp, $('span', { class: 'ledger-why', text: 'kept in this browser only, and sent with approve / open / close' }),
    $('span', { class: 'ledger-acts' }, button('use it', use), button('not now', () => { retry = null; said = null; load(); })));
}

function draw(ci, chores, prs) {
  if (!mount()) return;
  const open = prs.filter((p) => p.status === 'open'), merged = prs.filter((p) => p.status === 'merged').slice(-3).reverse();
  const moves = (p) => (p.ops || []).filter((o) => o.op === 'moved' && o.to);
  const rows = [];

  const out = (ci && ci.misplaced) || [];
  if (out.length) {
    rows.push($('h3', { class: 'ledger-h', text: 'out of place' }));
    for (const m of out) {
      const owed = merged.find((p) => moves(p).some((o) => o.object_id === m.object_id && o.to.zone === m.belongs_in));
      const asked = open.find((p) => moves(p).some((o) => o.object_id === m.object_id && o.to.zone === m.is_in));
      rows.push($('div', { class: 'ledger-row', 'data-kind': owed ? 'owed' : 'out' },
        object(m.object_id),
        $('span', { class: 'ledger-move mono', text: `zones/${m.is_in} → zones/${m.belongs_in}` }),
        $('span', { class: 'ledger-why', text: owed ? `pull request #${owed.id} said so — the roommate still has to carry it over`
          : asked ? `pull request #${asked.id} is open: approve it and main says zones/${m.is_in}`
          : `main says zones/${m.belongs_in}. A mess gets carried back.` }),
        !owed && !asked && button('I meant that', () => act('open a pull request', '/api/prs',
          { object_id: m.object_id, as_seen: true, title: `${m.object_id} lives in ${m.is_in} now` }),   // exactly where it is: approving leaves no drift
        `open a pull request: make main say zones/${m.is_in}`)));
    }
  }

  if (chores.length) {
    rows.push($('h3', { class: 'ledger-h', text: `chores · ${chores.length} it could not do itself` }));
    for (const c of chores) {
      rows.push($('div', { class: 'ledger-row', 'data-kind': 'chore' },
        object(c.object_id), $('span', { class: 'ledger-move mono', text: c.zone ? `zones/${c.zone}` : '' }),
        $('span', { class: 'ledger-why', text: `${VERDICT[c.verdict] || c.verdict || ''}${c.owner && c.owner !== 'shared' ? ` · ${c.owner}’s` : ''}` }),
        $('span', { class: 'ledger-at mono', text: c.opened_at ? `${c.id} · ${when(c.opened_at)}` : c.id })));
    }
  }

  if (open.length || merged.length || form) {
    rows.push($('h3', { class: 'ledger-h', text: 'pull requests · a change you meant' }));
    if (form) rows.push(formRow());
    for (const p of [...open, ...merged]) {
      const o = moves(p)[0];
      rows.push($('div', { class: 'ledger-row', 'data-kind': p.status === 'open' ? 'pr-open' : 'pr-merged' },
        $('span', { class: 'ledger-pr mono', text: `#${p.id}` }),
        $('span', { class: 'ledger-title', text: p.title }),
        $('span', { class: 'ledger-move mono', text: o ? `${o.object_id}  zones/${(o.from || {}).zone || '?'} → zones/${o.to.zone}` : '' }),
        p.status === 'open'
          ? $('span', { class: 'ledger-acts' },
            button('approve', () => act(`approve #${p.id}`, `/api/prs/${p.id}/approve`), 'merge into main: the roommate then carries it over'),
            button('close', () => act(`close #${p.id}`, `/api/prs/${p.id}/close`), 'decline it: nothing changes'))
          : $('span', { class: 'ledger-at mono', text: `merged ${String(p.merged_in || '').slice(0, 7)}` })));
    }
  }

  if (ci && ci.last_verified_job) {
    rows.push($('p', { class: 'ledger-proof mono', text: `verified by rescan: ${ci.last_verified_job} — a clean fresh pass came after it` }));
  }
  if (said) rows.push($('p', { class: 'ledger-said', role: 'status', text: said }));
  if (retry) rows.push(tokenRow());
  if (canOpen && !form) rows.push($('p', { class: 'ledger-new' }, $('button', { type: 'button', class: 'ledger-plus mono', text: '+ pull request', title: 'a change you MEAN: “move the lamp to the shelf”', onclick: openForm })));
  host.replaceChildren(...rows);
  host.hidden = !rows.length;
}

let again = 0;
async function load() {
  clearTimeout(again);
  const [ci, chores, prs] = await Promise.all([ask('/api/room/ci'), ask('/api/chores?status=open'), ask('/api/prs')]);
  canOpen = !!(prs.ok && prs.connected && Array.isArray(prs.body));      // roomctl's PR store answered: there is something to open one IN
  draw(ci.ok ? ci.body : null, chores.ok && Array.isArray(chores.body) ? chores.body : [], prs.ok && Array.isArray(prs.body) ? prs.body : []);
}
const typing = () => host && host.contains(document.activeElement) && /^(INPUT|SELECT)$/.test(document.activeElement.tagName);
const soon = () => { clearTimeout(again); again = setTimeout(() => (typing() ? soon() : load()), typing() ? 1500 : 250); };   // never redraw under someone's cursor

addEventListener('gitrl:room-state', soon);                     // dash.js fires it on every status render: the tree changed
(function listen(tries) {
  const es = window.gitrlEvents;                                // the page's ONE stream (dash.js opens it)
  if (!es) { if (tries < 40) setTimeout(() => listen(tries + 1), 250); return; }
  for (const name of ['chore', 'pr', 'room_state']) es.addEventListener(name, soon);
}(0));
