// ledger.js — the roommate's paperwork, under the CI badge on the dashboard (plan/roommate/03-interfaces.md §8).
//
//   OUT OF PLACE     one object, in a zone `main` does not have it in (/api/room/ci -> misplaced). Either it is a mess
//                    — the roommate carries it back — or you MEANT it, and that goes through a pull request.
//   CHORES           what the roommate could not put back itself (/api/chores?status=open — roomctl's ledger).
//   PULL REQUESTS    /api/prs — roomctl's own refs. Approving merges into `main`; the room has not moved yet, so the
//                    object shows up above as OWED until a clean fresh pass comes after the move.
//
// Nothing here is drawn until there is something to say: a clean room with no chores and no pull requests has no
// paperwork. Approve / close / open are local (or carry the cloud token): a visitor through the tunnel is told so.
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

async function ask(url, init) {
  try {
    const r = await fetch(url, { headers: { accept: 'application/json', ...(init ? { 'content-type': 'application/json' } : {}) }, ...init });
    let body = null;
    try { body = await r.json(); } catch { /* an empty body is an answer too */ }
    return { ok: r.ok, status: r.status, body };
  } catch (e) { return { ok: false, status: 0, body: { detail: String(e.message || e) } }; }
}

let host = null, busy = false, said = null;

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
  if (!r.ok) {
    said = r.status === 401 || r.status === 403 ? `${label}: that is done from the room’s own laptop — this visit came through the public address`
      : `${label}: ${(r.body && r.body.detail) || `the server answered ${r.status}`}`;
  }
  load();
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
          { object_id: m.object_id, zone: m.is_in, title: `${m.object_id} lives in ${m.is_in} now` }),
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

  if (open.length || merged.length) {
    rows.push($('h3', { class: 'ledger-h', text: 'pull requests · a change you meant' }));
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
  host.replaceChildren(...rows);
  host.hidden = !rows.length;
}

let again = 0;
async function load() {
  clearTimeout(again);
  const [ci, chores, prs] = await Promise.all([ask('/api/room/ci'), ask('/api/chores?status=open'), ask('/api/prs')]);
  draw(ci.ok ? ci.body : null, chores.ok && Array.isArray(chores.body) ? chores.body : [], prs.ok && Array.isArray(prs.body) ? prs.body : []);
}
const soon = () => { clearTimeout(again); again = setTimeout(load, 250); };

addEventListener('gitrl:room-state', soon);                     // dash.js fires it on every status render: the tree changed
(function listen(tries) {
  const es = window.gitrlEvents;                                // the page's ONE stream (dash.js opens it)
  if (!es) { if (tries < 40) setTimeout(() => listen(tries + 1), 250); return; }
  for (const name of ['chore', 'pr', 'room_state']) es.addEventListener(name, soon);
}(0));
