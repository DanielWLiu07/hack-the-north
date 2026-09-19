// dash.js — the dashboard sections below the GITRL hero: STATUS and SEARCH (web/PAGES.md).
//
// Plain DOM, no framework, no external requests. Works as a classic or a module script.
// It fills <section id="status"> and <section id="search"> inside <main id="dashboard">,
// creating whatever is missing.
//
// What the CLI cannot do, per section:
//   status — it is LIVE: the working tree changes on screen the moment the robot rescans (SSE).
//   search — it shows WHY a result matched. `matched_by` is rendered as badges in words, and the
//            case the whole Elastic story rests on (the vector leg found it, BM25 did not) is the
//            only thing on the page that gets the accent colour.
// Every string from the server goes in through textContent; nothing is ever parsed as HTML.
(() => {
  'use strict';

  // ---- tiny DOM helpers ---------------------------------------------------------------
  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) node.append(kid);
    return node;
  }
  const clear = (node) => { while (node.firstChild) node.firstChild.remove(); return node; };
  const short = (sha) => (sha ? String(sha).slice(0, 7) : '—');

  function ago(ts) {
    const t = Date.parse(ts);
    if (!Number.isFinite(t)) return '—';
    const s = Math.round((Date.now() - t) / 1000);
    if (s < 0) return 'just now';
    if (s < 60) return `${s} s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  }
  const when = (ts) => el('time', { datetime: ts, title: ts, 'data-ago': ts, text: ago(ts) });
  setInterval(() => document.querySelectorAll('#dashboard time[data-ago]').forEach((n) => {
    n.textContent = ago(n.getAttribute('data-ago'));
  }), 30000);

  // fetch JSON; failures carry the server's one error shape { error, detail, retryable } (§2.7)
  async function getJSON(url, signal, post) {
    let r;
    try { r = await fetch(url, post === undefined ? { signal, headers: { Accept: 'application/json' } }
      : { method: 'POST', signal, headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify(post) }); }
    catch (e) { if (e.name === 'AbortError') throw e; throw { error: 'server_unreachable', detail: 'the web server did not answer', retryable: true }; }
    let body = null;
    try { body = await r.json(); } catch { /* not JSON */ }
    if (!r.ok) throw { status: r.status, error: (body && body.error) || `http_${r.status}`, detail: (body && body.detail) || r.statusText, retryable: !!(body && body.retryable) };
    return body;
  }

  // ---- scaffold -----------------------------------------------------------------------
  function mount() {
    let main = document.getElementById('dashboard');
    if (!main) { main = el('main', { id: 'dashboard' }); document.body.append(main); }
    const section = (id, label) => {
      let s = document.getElementById(id);
      if (!s) { s = el('section', { id }); main.append(s); }
      s.setAttribute('aria-labelledby', `${id}-h`);
      clear(s).append(el('h2', { id: `${id}-h`, text: label }));
      return s;
    };
    const status = section('status', 'Is the room at main?');     // order on the page: status, then search
    const search = section('search', 'Where did I leave it?');
    if (status.compareDocumentPosition(search) & Node.DOCUMENT_POSITION_PRECEDING) main.insertBefore(status, search);
    buildStatus(status);
    buildSearch(search);
  }

  // ======================================================================================
  // STATUS — branch, HEAD, clean / dirty / conflict, the changed objects. Live over SSE.
  // ======================================================================================
  function buildStatus(root) {
    const word = el('span', { text: '…' });
    const bar = el('div', { class: 'status-bar', 'data-state': 'unknown', role: 'status', 'aria-live': 'polite' },
      el('div', { class: 'state-word' }, el('span', { class: 'led', 'aria-hidden': 'true' }), word));
    const kv = (label) => { const v = el('span', { text: '—' }); bar.append(el('div', { class: 'kv' }, el('span', { text: label }), v)); return v; };
    const branch = kv('branch'), head = kv('HEAD'), capture = kv('last capture');
    const conn = el('div', { class: 'conn', 'data-conn': 'connecting', text: 'connecting' });
    bar.append(conn);
    // THE CI BADGE FOR A ROOM. `main` is where everything belongs; a caretaker keeps the room there. Green is
    // git's own sentence — "nothing to commit, working tree clean" — and red is the drift, in git-status words.
    const ciWord = el('strong', { class: 'ci-word', text: '…' }), ciLine = el('span', { class: 'ci-line', text: 'asking the room' });
    const ciSince = el('span', { class: 'ci-since mono' });
    const ci = el('div', { class: 'ci', 'data-ci': 'unknown', role: 'status', 'aria-live': 'polite' },
      el('span', { class: 'ci-mark', 'aria-hidden': 'true' }), el('span', { class: 'ci-name mono', text: 'room-clean' }), ciWord, ciLine, ciSince);
    const changes = el('ul', { class: 'changes', 'aria-label': 'What drifted from main' });
    const job = el('div', { class: 'job', hidden: true });
    const note = el('p', { class: 'note', hidden: true });
    // Recent captures, newest first. A REJECTED one is the way into /capture/<id>, the page
    // that explains why a diff was wrong — it must be one click from here, not a typed URL.
    const captures = el('div', { class: 'captures', hidden: true });
    root.append(ci, bar, changes, captures, job, note);

    async function loadCaptures() {
      let d;
      try { d = await getJSON('/api/captures?limit=8'); } catch { captures.hidden = true; return; }
      const list = d.captures || [];
      captures.hidden = !list.length;
      clear(captures).append(el('span', { class: 'captures-label', text: 'recent captures' }));
      for (const c of list) {
        const rejected = c.gate_pass === false;
        captures.append(el('a', {
          class: 'capture-chip mono', href: `/capture/${encodeURIComponent(c.capture_id)}`,
          'data-gate': rejected ? 'reject' : c.gate_pass === true ? 'pass' : 'unknown',
          title: rejected ? 'the quality gate rejected this capture — see why' : 'open this capture',
        }, el('span', { text: c.capture_id }),
           el('span', { class: 'gate', text: rejected ? 'REJECTED — why?' : c.gate_pass === true ? 'pass' : '—' })));
      }
      if (d.source === 'fixture') captures.append(el('span', { class: 'captures-note', text: 'fixture data' }));
    }

    let conflict = null;

    const GIT_WORD = { moved: 'modified', changed: 'modified', modified: 'modified', added: 'untracked', untracked: 'untracked', removed: 'deleted', deleted: 'deleted', missing: 'deleted' };
    function render(s) {
      const state = conflict ? 'conflict' : s.clean ? 'clean' : 'dirty';
      bar.dataset.state = state;
      const n = (s.changes || []).length;
      ci.dataset.ci = state === 'clean' ? 'pass' : 'fail';
      window.dispatchEvent(new CustomEvent('gitrl:room-state', { detail: { state, changes: s.changes || [], branch: s.branch || 'main' } }));
      ciWord.textContent = state === 'clean' ? 'passing' : 'failing';
      ciLine.textContent = state === 'conflict' ? 'two roommates moved the same thing — a merge conflict, not a mess'
        : state === 'clean' ? `nothing to commit, working tree clean — the room is at ${s.branch || 'main'}`
        : `${n} thing${n === 1 ? ' has' : 's have'} drifted from ${s.branch || 'main'}. A mess gets put back; a decision goes through a pull request.`;
      ciSince.textContent = s.since ? `since ${when(s.since)}` : s.heartbeat && s.heartbeat.at ? `heartbeat ${s.heartbeat.last} · ${when(s.heartbeat.at)}` : '';
      word.textContent = state === 'conflict' ? 'merge conflict' : state === 'clean' ? 'clean' : `${n} change${n === 1 ? '' : 's'}`;
      branch.textContent = s.branch || '—';
      head.textContent = short(s.head);
      clear(capture).append(s.last_capture ? when(s.last_capture) : '—');
      clear(changes);
      for (const c of s.changes || []) {
        const cm = typeof c.delta_m === 'number' ? `moved ${(c.delta_m * 100).toFixed(c.delta_m < 0.1 ? 1 : 0)} cm` : '';
        changes.append(el('li', { class: 'change', 'data-type': c.type },
          el('span', { class: 'type', text: `${GIT_WORD[c.type] || c.type}:` }),
          el('a', { class: 'oid mono', href: `/object/${encodeURIComponent(c.object_id)}`, text: c.object_id }),
          el('span', { class: 'zone', text: c.zone ? `zones/${c.zone}` : '' }),
          el('span', { class: 'delta', text: cm })));
      }
      if (conflict) {
        changes.prepend(el('li', { class: 'change', 'data-type': 'conflict' },
          el('span', { class: 'type', text: 'conflict' }),
          el('a', { class: 'oid mono', href: `/object/${encodeURIComponent(conflict.object_id)}`, text: conflict.object_id }),
          el('span', { class: 'zone', text: 'moved on both branches' }), el('span', { class: 'delta', text: 'ours / theirs' })));
      }
    }

    let refetch = 0;
    async function load(pulse) {
      clearTimeout(refetch);
      try {
        let s = null;
        try { const c = await getJSON('/api/room/ci'); if (c && c.state && c.state !== 'unknown') s = { ...c, clean: c.state === 'clean', branch: c.branch || 'main' }; } catch { /* not mounted yet: git's own status below */ }
        render(s && Array.isArray(s.changes) ? s : { ...(await getJSON('/api/status')), ...(s ? { since: s.since, heartbeat: s.heartbeat } : {}) });
        note.hidden = true;
        loadCaptures();
        if (pulse) { bar.classList.remove('pulse'); void bar.offsetWidth; bar.classList.add('pulse'); }
      } catch (e) {
        bar.dataset.state = 'unknown';
        word.textContent = 'status unavailable';
        note.hidden = false;
        note.textContent = e.status === 404 ? 'GET /api/status is not live on this server yet.' : `${e.error}: ${e.detail}`;
      }
    }
    const soon = (pulse) => { clearTimeout(refetch); refetch = setTimeout(() => load(pulse), 120); };

    // SSE, not a WebSocket: server -> browser only, and EventSource reconnects by itself
    // (with Last-Event-ID, so nothing is missed). It gives up only on an HTTP error, e.g. the
    // endpoint not existing yet — then we retry slowly ourselves.
    function listen() {
      if (!('EventSource' in window)) { conn.dataset.conn = 'off'; conn.textContent = 'live updates unsupported'; return; }
      const es = new EventSource('/api/events');
      window.gitrlEvents = es;                             // ONE stream for the page: the graph and the Point buttons listen on it too
      const data = (ev) => { try { return JSON.parse(ev.data); } catch { return {}; } };
      es.onopen = () => { conn.dataset.conn = 'live'; conn.textContent = 'live'; };
      es.onerror = () => {
        conn.dataset.conn = 'reconnecting'; conn.textContent = 'reconnecting';
        if (es.readyState === EventSource.CLOSED) { conn.dataset.conn = 'off'; conn.textContent = 'offline — retrying'; setTimeout(listen, 5000); }
      };
      es.addEventListener('status', () => soon(false));          // the event is a summary; the list comes from /api/status
      es.addEventListener('room_state', () => soon(true));       // the watch loop's verdict (plan/roommate 03 §8)
      es.addEventListener('capture', () => soon(true));
      es.addEventListener('conflict', (ev) => { conflict = data(ev); soon(true); });
      es.addEventListener('job', (ev) => {
        const j = data(ev);
        job.hidden = false;
        clear(job).append(`${j.id || 'job'} · ${j.state || ''}`,
          typeof j.progress === 'number' ? el('progress', { max: 1, value: j.progress }) : null);
        if (j.state === 'done' || j.state === 'failed') { if (j.state === 'done') conflict = null; setTimeout(() => { job.hidden = true; }, 4000); soon(true); }
      });
    }

    load(false);
    listen();
  }

  // ======================================================================================
  // SEARCH — the hybrid query box, with match provenance as visible badges.
  // ======================================================================================
  const STOP = new Set('a an and are at did do for i in is it left leave me my of on the to was where which with'.split(' '));
  const terms = (q) => [...new Set(q.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((w) => w.length > 1 && !STOP.has(w)))];

  // a description with the query's words marked — so it is VISIBLE when none of them is there
  function marked(text, words) {
    const li = el('li');
    if (!words.length) { li.textContent = text; return li; }
    const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'giu');
    let last = 0;
    for (const m of text.matchAll(re)) {
      if (m.index > last) li.append(text.slice(last, m.index));
      li.append(el('mark', { text: m[0] }));
      last = m.index + m[0].length;
    }
    li.append(text.slice(last));
    return li;
  }

  function badge(kind, hit, label, detail) {
    return el('span', { class: 'badge', 'data-kind': kind, 'data-hit': String(hit) },
      `${label} ${hit ? '✓' : '✗'}`, detail ? el('small', { text: detail }) : null,
      el('span', { class: 'sr-only', text: hit ? ' matched' : ' did not match' }));
  }

  function card(r, words, head) {
    const m = r.matched_by || {};
    const vectorOnly = m.vector === true && m.bm25 === false;
    const seen = r.last_seen || {};
    const timeline = r.timeline || [];
    const said = [r.class || '', ...(r.descriptions || [])].join(' ').toLowerCase();
    const synthetic = !r.provenance || r.provenance.synthetic;      // no provenance at all is not proof of a camera
    const saysIt = words.some((w) => said.includes(w));

    const badges = el('div', { class: 'badges', role: 'group', 'aria-label': 'Why this matched' },
      badge('bm25', !!m.bm25, 'BM25', m.bm25_rank ? `#${m.bm25_rank}` : null),
      badge('vector', !!m.vector, 'VECTOR', m.vector_rank ? `#${m.vector_rank}` : null),
      el('span', { class: 'badge', 'data-kind': 'rerank' }, m.rerank_position ? `RERANK #${m.rerank_position}` : 'RERANK off'));

    return el('li', { class: 'card', 'data-vector-only': String(vectorOnly) },
      el('div', {},
        el('h3', {}, el('a', { href: `/object/${encodeURIComponent(r.object_id)}`, text: r.class || r.object_id }),
          el('span', { class: 'presence', 'data-present': String(!!r.present_now), text: r.present_now ? 'HERE NOW' : 'ABSENT' })),
        el('div', { class: 'oid', text: r.object_id })),
      badges,
      pointAction(r),
      el('div', { class: 'seen' }, 'last seen in ', el('b', { text: seen.zone ? `zones/${seen.zone}` : '—' }), ' · ', when(seen.ts),
        ' · commit ', el('span', { class: 'mono', text: short(seen.commit_sha) }),
        seen.branch && seen.branch !== 'main' ? ` (${seen.branch})` : '',
        seen.capture_id ? [' · ', el('a', { class: 'mono', href: `/capture/${encodeURIComponent(seen.capture_id)}`, text: seen.capture_id })] : null),
      vectorOnly ? el('p', { class: 'why' },
        el('strong', { text: 'The vector leg found this. BM25 did not. ' }),
        saysIt ? 'Lexical search alone would have missed this.'
          : `Nothing it has ever been called contains “${words.join('” or “')}” — lexical search alone would have missed this.`) : null,
      synthetic ? el('p', { class: 'synthetic', role: 'note' }, el('b', { class: 'synthetic-chip', text: 'SYNTHETIC' }), ` ${(r.provenance && r.provenance.why) || 'this server did not report who wrote the text — treated as scripted'}`) : null,
      el('div', { class: 'descriptions-label', text: `${synthetic && (!r.provenance || r.provenance.scripted_text) ? 'what the script had each camera call it' : 'what the cameras called it'}${
        (r.descriptions || []).length > 1 ? ` · ${r.descriptions.length} descriptions, no two alike` : ''}` }),
      el('ul', { class: 'descriptions' }, (r.descriptions || []).map((d) => marked(d, words))),
      el('div', { class: 'timeline-label', text: `in ${timeline.length} commit${timeline.length === 1 ? '' : 's'}` }),
      el('div', { class: 'timeline' }, timeline.slice(0, 8).map((t) => el('span', { 'data-head': String(t.commit_sha === head), title: t.ts,
        text: `${short(t.commit_sha)} ${t.branch && t.branch !== 'main' ? t.branch + ' ' : ''}· ${t.zone || '—'}` })),
        timeline.length > 8 ? el('span', { text: `+${timeline.length - 8}` }) : null));
  }

  // The first demo beat: the search found it, the roommate goes and POINTS at it. One explicit button (armed after
  // a moment, deaf to double-clicks); POST /api/object-life/{id}/point plans the job; its state then arrives over
  // the page's SSE stream as `job` events. With no executor connected it says so — it never pretends the robot moved.
  const TERMINAL = /^(done|succeeded|failed|cancelled|rejected|undelivered|unknown)/;
  // Hooks for a visual (the 3D roommate): plain DOM events on window, poses in the ROOM frame (world_z_up, metres).
  //   gitrl:point      {object_id, class, zone, pose{x,y,z,yaw}, job_id, state, executor, frame}   a Point job was planned
  //   gitrl:job        {job_id, state, progress, object_id}                                     its state changed
  //   gitrl:room-state {state: clean|dirty|conflict, changes[], branch}                         the CI badge changed
  const tell = (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail }));
  function pointAction(r) {
    const seen = r.last_seen || {}, can = !!r.present_now && !!seen.pose;
    const line = el('span', { class: 'point-line', role: 'status', 'aria-live': 'polite',
      text: can ? '' : r.present_now ? 'no pose recorded for it, so there is nowhere to point' : 'it is not in the room now, so there is nothing to point at' });
    const btn = el('button', { class: 'point-btn', type: 'button', disabled: true, text: 'Point at it' });
    if (can) setTimeout(() => { btn.disabled = false; }, 600);
    let watching = null, poll = 0;
    const show = (j) => {
      const state = String(j.state || '');
      line.dataset.state = /fail|reject|undelivered|unknown/.test(state) ? 'bad' : TERMINAL.test(state) ? 'done' : 'live';
      // in the roommate's words, and never more than the job itself says: "unknown" means it MAY have moved
      const said = j.executor === 'not_connected' ? ' — planned; no robot is connected to this server yet, so nothing moved'
        : /^dispatch/.test(state) ? ' — sent to the robot; waiting for it to finish' : /^succeeded|^done/.test(state) ? ' — the roommate went over and pointed at it'
        : /^undelivered/.test(state) ? ' — the robot could not be reached, so nothing moved' : /^unknown/.test(state) ? ' — sent, but no answer came back: it may or may not have moved'
        : /^fail/.test(state) ? ` — ${j.message || j.error || 'the robot could not do it'}` : '';
      line.textContent = `${j.job_id || j.id} · ${state}${typeof j.progress === 'number' && j.progress > 0 && j.progress < 1 ? ` · ${Math.round(j.progress * 100)}%` : ''}${said}`;
      if (TERMINAL.test(state)) { btn.disabled = false; clearInterval(poll); }
      tell('gitrl:job', { job_id: j.job_id || j.id, state, progress: j.progress, object_id: r.object_id });
    };
    btn.addEventListener('click', async (e) => {
      if (e.detail > 1 || btn.disabled) return;
      btn.disabled = true; line.dataset.state = 'live'; line.textContent = 'asking the roommate…';
      try {
        const job = await getJSON(`/api/object-life/${encodeURIComponent(r.object_id)}/point`, undefined, {});
        watching = job.job_id; show(job);
        tell('gitrl:point', { object_id: r.object_id, class: r.class, zone: job.zone || seen.zone, pose: job.target_pose || seen.pose, job_id: job.job_id,
          state: job.state, executor: job.executor, frame: 'world_z_up' });
        const p = job.target_pose; if (p) line.title = `target: zones/${job.zone || '—'} (${[p.x, p.y, p.z].map((v) => Number(v).toFixed(2)).join(', ')}) · about ${job.estimated_s} s`;
        if (job.executor === 'not_connected') setTimeout(() => { btn.disabled = false; }, 1500);
        if (job.dispatch && job.dispatch.dispatched) {     // only a DISPATCHED job is stored: ask for it too, in case an event is missed
          clearInterval(poll); let tries = 0;
          poll = setInterval(async () => {
            if (++tries > 90) return clearInterval(poll);
            try { show(await getJSON(`/api/jobs/${watching}`)); } catch (e) { if (e.status === 404) clearInterval(poll); /* the stream stays the primary source */ }
          }, 2000);
        }
      } catch (err) { line.dataset.state = 'bad'; line.textContent = `${err.error}: ${err.detail}`; btn.disabled = false; }
    });
    const es = window.gitrlEvents;
    if (es) es.addEventListener('job', (ev) => { let j; try { j = JSON.parse(ev.data); } catch { return; } if (watching && (j.id === watching || j.job_id === watching)) show(j); });
    return el('div', { class: 'point' }, btn, el('a', { class: 'point-more', href: `/object/${encodeURIComponent(r.object_id)}`, text: 'its whole life →' }), line);
  }

  const ES_DOWN = new Set(['elastic_unreachable', 'elastic_timeout', 'elastic_unconfigured', 'elastic_auth', 'elastic_error']);

  function buildSearch(root) {
    const input = el('input', { type: 'search', id: 'q', name: 'q', autocomplete: 'off', spellcheck: 'false', enterkeyhint: 'search',
      placeholder: 'where are my keys', 'aria-describedby': 'search-meta', 'data-sentry-unmask': true });
    const past = el('input', { type: 'checkbox', checked: true });
    const meta = el('div', { class: 'meta', id: 'search-meta', role: 'status', 'aria-live': 'polite' });
    const out = el('div', {});
    const form = el('form', { role: 'search', onsubmit: (e) => { e.preventDefault(); run(true); } },
      el('label', { class: 'sr-only', for: 'q', text: 'Search every object that has ever been in the room' }),
      el('div', { class: 'query' },
        el('span', { 'aria-hidden': 'true' }, svgLens()), input),
      el('div', { class: 'under' }, 'try',
        ['where are my keys', 'mug', 'where did I leave my hammer', 'something to write with'].map((q) =>
          el('button', { type: 'button', class: 'chip', text: q, onclick: () => { input.value = q; input.focus(); run(true); } })),
        el('label', { class: 'toggle' }, past, 'search the past too')));
    const stage = el('div', { id: 'roommate-stage', class: 'roommate-stage', hidden: true, 'aria-hidden': 'true' });   // a visual may mount here (see the hooks above)
    root.append(form, stage, meta, out);

    let timer = 0, ctl = null, seq = 0;

    function idle() {
      meta.textContent = '';
      clear(out).append(el('div', { class: 'state' },
        el('h3', { text: 'Ask the roommate where it is.' }),
        el('p', { text: 'The roommate remembers where everything belongs, and where it last was. Ask in your own words — “where are my keys” — and it can go and point at them.' }),
        el('p', { text: 'Underneath: hybrid search over every object the room has ever held — BM25 on the label, Jina dense vectors on what each camera said it saw, fused with RRF, then a cross-encoder rerank. Each result shows which leg found it.' })));
    }

    async function run(now) {
      clearTimeout(timer);
      const q = input.value.trim();
      const url = new URL(location.href);
      if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
      history.replaceState(null, '', url);
      if (ctl) ctl.abort();
      if (!q) return idle();
      if (!now) { timer = setTimeout(() => run(true), 280); return; }

      const mine = ++seq;
      ctl = new AbortController();
      meta.textContent = '';
      clear(out).append(el('div', { class: 'state loading', text: 'asking Elasticsearch' }));
      try {
        const r = await getJSON(`/api/search?q=${encodeURIComponent(q)}&limit=20&all_time=${past.checked}`, ctl.signal);
        if (mine !== seq) return;                                    // a newer query has been sent
        const n = r.results.length, words = terms(q);
        meta.textContent = `${n} object${n === 1 ? '' : 's'} · ${r.retriever || ''} · BM25 on ${(r.bm25_fields || []).join(', ') || '—'}`
          + ` · ${r.took_ms ?? '—'} ms` + (r.reranked === false ? ' · reranker unavailable: RRF order' : '');
        if (!n) {
          clear(out).append(el('div', { class: 'state' }, el('h3', { text: `Nothing in the room${past.checked ? ', now or ever,' : ' right now'} matches “${q}”.` }),
            past.checked ? null : el('p', { text: 'It may have been here before — tick “search the past too”.' })));
          return;
        }
        // what is real here and what is generated — said ONCE, above the results, whenever any of them is scripted
        const pv = r.provenance, fake = pv ? pv.synthetic_results : r.results.length;
        const legend = fake ? el('aside', { class: 'legend-prov', role: 'note', 'aria-label': 'What is real and what is generated' },
          el('p', {}, el('b', { class: 'synthetic-chip', text: 'SYNTHETIC' }), ` ${fake} of ${n} result${n === 1 ? '' : 's'} rank${fake === 1 ? 's' : ''} text that a script wrote, not a camera.`),
          el('p', {}, el('b', { text: 'Real: ' }), (pv && pv.legend.real) || 'the search itself', '.'),
          el('p', {}, el('b', { text: 'Generated: ' }), (pv && pv.legend.generated) || 'the descriptions', '.')) : null;
        clear(out).append(...[legend, el('ol', { class: 'results' }, r.results.map((x) => card(x, words, r.head)))].filter(Boolean));
      } catch (e) {
        if (e.name === 'AbortError' || mine !== seq) return;
        meta.textContent = '';
        const down = ES_DOWN.has(e.error);
        clear(out).append(el('div', { class: 'state', role: 'alert' },
          el('h3', { text: e.error === 'elastic_paused' ? 'Search is parked.' : down ? 'Elasticsearch is not answering.' : e.status === 404 ? 'Search is not live on this server yet.' : 'The search failed.' }),
          el('p', {}, el('code', { text: e.error }), ' ', e.detail || ''),
          e.error === 'elastic_paused' ? el('p', { text: 'Deliberate: the Elasticsearch key is parked to save quota before the demo, and this server makes no calls while it is. Search runs the real hybrid retriever or nothing — it never fakes results from fixtures.' })
            : down ? el('p', { text: 'The web server is up; the cluster behind it is not. Nothing on this page is faked — no cluster, no results.' }) : null,
          el('button', { type: 'button', class: 'chip', text: 'try again', onclick: () => run(true) })));
      }
    }

    input.addEventListener('input', () => run(false));
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape' && input.value) { input.value = ''; run(true); } });
    past.addEventListener('change', () => run(true));

    const q0 = new URLSearchParams(location.search).get('q');
    if (q0) { input.value = q0; run(true); root.scrollIntoView(); } else idle();
  }

  function svgLens() {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke-width', '2'); svg.setAttribute('stroke-linecap', 'round');
    const c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', '11'); c.setAttribute('cy', '11'); c.setAttribute('r', '7');
    const l = document.createElementNS(ns, 'path'); l.setAttribute('d', 'M20 20l-3.6-3.6');
    svg.append(c, l);
    return svg;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();
})();
