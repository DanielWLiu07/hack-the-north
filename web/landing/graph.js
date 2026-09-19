// graph.js — HISTORY: the room's commit graph as a control surface (docs/24 Part B).
//
// `git log --graph` for a room. Git is the source (GET /api/graph is a real `git log`),
// Elasticsearch is the enrichment: what each commit changed, which capture built it, and
// the capture that was REJECTED before it — drawn in the page's one accent and linked to
// /capture/<id>, which is what turns the log into a diagnosis.
//
// PREVIEW BEFORE EXECUTE, ALWAYS. Selecting a node only opens a preview of that state.
// Running it is a second, separate control inside the preview, which arms itself 600 ms
// after the preview opens and ignores the second click of a double-click. A robot that
// moves on one click is a robot that moves when someone brushes the trackpad.
//
// No graph library: a commit DAG must respect time order, so rows ARE time and the lane
// assignment is the standard railroad walk (layout(), below).

(() => {
  const root = document.getElementById('history');
  if (!root) return;

  const LANE_W = () => (innerWidth < 560 ? 18 : 26), NODE_Y = 27, ARM_MS = 600;
  const SVG = 'http://www.w3.org/2000/svg';

  // ---- tiny DOM helpers (text only ever goes in as text) -----------------------------
  function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === 'text') n.textContent = v; else if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v === true ? '' : v);
    }
    n.append(...kids.filter((c) => c != null && c !== false));
    return n;
  }
  const svg = (tag, attrs) => { const n = document.createElementNS(SVG, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };
  const short = (sha) => (sha ? sha.slice(0, 7) : '—');
  const plural = (n, one, many = one + 's') => `${n} ${n === 1 ? one : many}`;
  function ago(iso) {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!isFinite(s)) return '';
    const a = Math.abs(s), w = a < 90 ? [Math.round(a), 's'] : a < 5400 ? [Math.round(a / 60), ' min']
      : a < 129600 ? [Math.round(a / 3600), ' h'] : [Math.round(a / 86400), ' d'];
    return s < 0 ? `in ${w[0]}${w[1]}` : `${w[0]}${w[1]} ago`;
  }
  async function getJSON(url, init) {
    const r = await fetch(url, init);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.detail || r.statusText), { status: r.status, error: body.error || 'http_error', detail: body.detail || r.statusText });
    return body;
  }

  // ---- the railroad layout -------------------------------------------------------------
  // Rows are newest -> oldest (the server's --date-order guarantees a parent never comes
  // before its children). lanes[i] holds the sha lane i is waiting for. A commit takes the
  // first lane waiting for it (= its first child's lane) or, if it is a branch tip, the
  // first free one; every other lane waiting for it merges in and is freed; its first
  // parent continues its lane and each further parent opens (or joins) another.
  function layout(nodes) {
    const lanes = [], place = new Map(), edges = [];
    nodes.forEach((n, row) => {
      let lane = lanes.indexOf(n.sha);
      if (lane < 0) { lane = lanes.indexOf(null); if (lane < 0) lane = lanes.length; }
      lanes.forEach((waiting, i) => { if (waiting === n.sha && i !== lane) lanes[i] = null; });
      place.set(n.sha, { lane, row });
      lanes[lane] = null;
      n.parents.forEach((p, k) => {
        let via = lane;                                   // the first parent continues MY lane, always:
        if (k > 0) {                                      // a fork is a rail down to the fork point
          via = lanes.indexOf(p);                         // a merge parent joins a lane already heading
          if (via < 0) { via = lanes.indexOf(null); if (via < 0) via = lanes.length; }   // there, or opens one
        }
        lanes[via] = p;
        edges.push({ child: n.sha, parent: p, via });
      });
    });
    return { place, edges, width: Math.max(1, ...[...place.values()].map((p) => p.lane + 1)) };
  }
  const DASH = ['', '7 5', '2 5', '11 4 2 4'];            // branches differ by stroke, not by colour

  // ---- state -------------------------------------------------------------------------------
  let data = null, selected = null, compareTo = null, compareMode = false, known = new Set(), jobs = new Map();
  let armTimer = 0, currentJob = null;

  const h2 = el('h2', { text: 'History — the room’s commit graph' });
  const lead = el('p', { class: 'g-lead' },
    'Select a commit to ', el('strong', { text: 'preview' }), ' that state. Nothing moves until you run it from the preview.');
  const tag = el('span', { class: 'g-tag', hidden: true });
  const compareBtn = el('button', { class: 'g-toggle', type: 'button', 'aria-pressed': 'false', text: 'compare two commits',
    onclick: () => { compareMode = !compareMode; compareBtn.setAttribute('aria-pressed', String(compareMode)); if (!compareMode) { compareTo = null; renderPreview(); paint(); } hint(); } });
  const tools = el('div', { class: 'g-tools' }, compareBtn, tag);
  const rails = svg('svg', { class: 'g-rails', 'aria-hidden': 'true', focusable: 'false' });
  const rows = el('ol', { class: 'g-rows' });
  const graph = el('div', { class: 'g-graph' }, rails, rows);
  const preview = el('aside', { class: 'g-preview', 'aria-live': 'polite', 'aria-label': 'Preview of the selected commit' });
  const note = el('p', { class: 'g-note', hidden: true });
  // the room itself, from above (roommap.js): a graph of a ROOM is only a control surface if you can see the room
  const mapBox = el('div', { class: 'g-map' });
  const scrub = el('input', { class: 'g-scrub', type: 'range', min: 0, max: 0, step: 1, value: 0, 'aria-label': 'Scrub the room through its commits, oldest to newest. Nothing physical moves.' });
  const scrubLabel = el('p', { class: 'g-scrub-label mono', text: '' });
  const mapWrap = el('section', { class: 'g-mapwrap', 'aria-label': 'The room from above' }, mapBox,
    el('div', { class: 'g-scrubrow' }, el('span', { class: 'g-scrub-k', text: 'scrub time' }), scrub, el('span', { class: 'g-scrub-k', text: 'now' })), scrubLabel);
  // "clean up my room", in git's words: the room has a CLEAN commit (a tag), mess is a diff against it, and
  // cleaning up is `git restore`. One button — and the sentence it sends is plain English, through the middleware.
  const tidy = el('section', { class: 'g-tidy', hidden: true, 'aria-label': 'Room clean-up' });
  root.append(h2, lead, tools, tidy, el('div', { class: 'g-wrap' }, graph, el('div', { class: 'g-side' }, mapWrap, preview)), note);
  const map = window.gitrlRoomMap ? window.gitrlRoomMap.create(mapBox) : null;

  // ---- what the map shows: a scrubbed moment > a command's plan > a hovered / selected commit > the room now --------
  const states = new Map();                               // sha -> Promise<state>: commits are immutable
  const stateOf = (sha) => { if (!states.has(sha)) states.set(sha, getJSON(`/api/state?ref=${sha}`).catch((e) => { states.delete(sha); throw e; })); return states.get(sha); };
  const nameOf = (sha) => { const n = data && data.nodes.find((x) => x.sha === sha); return n ? `${short(sha)} · ${n.subject}` : short(sha); };
  let planShown = null, peek = null, scrubbing = null, mapToken = 0, mapHead = null, peekTimer = 0;
  async function syncMap() {
    if (!map || !data || !data.head) return;
    const token = ++mapToken;
    try {
      const headState = await stateOf(data.head);
      if (token !== mapToken) return;
      if (mapHead !== data.head) { mapHead = data.head; map.setBase(headState); }
      if (scrubbing) { const st = await stateOf(scrubbing); if (token === mapToken) map.state(st, 'THE ROOM THEN', nameOf(scrubbing)); return; }
      if (planShown) { map.plan(planShown.ops, planShown.conflicts, 'THE PLAN', planShown.label); return; }
      const target = peek || (compareTo ? null : selected);
      if (target && target !== data.head) {
        const st = await stateOf(target);
        if (token === mapToken) map.diff(st, peek && peek !== selected ? 'IF THE ROOM WENT BACK TO' : 'TO MAKE THE ROOM THIS AGAIN', nameOf(target));
        return;
      }
      map.now(`HEAD ${short(data.head)} · ${headState.objects.length} objects`);
    } catch (e) { console.warn('[graph] the room map could not be drawn — the list and the console still work:', e && (e.detail || e.message || e)); }
  }
  function setPeek(sha) { clearTimeout(peekTimer); peekTimer = setTimeout(() => { if (peek !== sha) { peek = sha; syncMap(); } }, sha ? 110 : 60); }
  if (map) map.onHover = (id) => { for (const li of preview.querySelectorAll('.g-op[data-id]')) li.toggleAttribute('data-hot', !!id && li.dataset.id === id); };

  function trunkOldestFirst() { return data ? [...(data.trunk || [])].reverse() : []; }
  function paintScrub() {
    const t = trunkOldestFirst(), i = scrubbing ? t.indexOf(scrubbing) : t.length - 1, n = data && data.nodes.find((x) => x.sha === t[i]);
    scrubLabel.textContent = !t.length ? '' : scrubbing ? `commit ${i + 1} of ${t.length} · ${short(t[i])} · ${n ? n.subject : ''} · ${n ? ago(n.ts) : ''} — nothing moves` : `${t.length} commits on ${data.branch || 'this branch'} — drag to watch the room change`;
    for (const li of rows.children) li.toggleAttribute('data-scrub', !!scrubbing && li.dataset.sha === scrubbing);
  }
  scrub.addEventListener('input', () => { const t = trunkOldestFirst(), sha = t[Number(scrub.value)]; scrubbing = sha && sha !== data.head ? sha : null; paintScrub(); syncMap(); });

  function hint() {
    if (selected) return;
    preview.replaceChildren(el('div', { class: 'g-empty' },
      el('p', { class: 'g-empty-title', text: compareMode ? 'Pick two commits.' : 'Pick a commit.' }),
      el('p', { text: compareMode ? 'The object-level diff between any two moments of the room.'
        : 'First you see what that state is and what it would take to get there. Only then can you run it.' })));
  }

  // ---- rows -----------------------------------------------------------------------------------
  function changeSummary(c) {
    if (!c) return null;
    const bits = [['+', c.added, 'added'], ['~', c.moved, 'moved'], ['−', c.removed, 'removed']]
      .filter(([, list]) => list && list.length)
      .map(([sym, list, word]) => el('span', { class: 'g-delta', title: `${word}: ${list.join(', ')}`, text: `${sym}${list.length}` }));
    return bits.length ? el('span', { class: 'g-deltas', 'aria-label': bits.map((b) => b.title).join('; ') }, ...bits) : null;
  }
  function failingText(r) {
    const f = (r.failing || [])[0];
    return f ? `${f.name} ${Number(f.value).toPrecision(3)} ${f.unit} (limit ${f.limit})` : (r.outcome || 'quality gate');
  }

  function renderRows() {
    rows.replaceChildren(...data.nodes.map((n) => {
      const isHead = n.sha === data.head, rejected = n.rejected_before || [];
      const refs = (n.refs || []).filter((r) => r.kind !== 'remote' && !/^origin(\/|$)/.test(r.name))      // origin/* repeats the local branches
        .filter((r) => r.kind !== 'head' || !n.refs.some((o) => o.head && o.kind === 'branch'))
        .map((r) => el('span', { class: 'g-ref', 'data-kind': r.kind, 'data-head': r.head ? '' : null, text: (r.head && r.kind === 'branch' ? 'HEAD → ' : '') + r.name }));
      const pick = el('button', { class: 'g-pick', type: 'button', 'aria-pressed': 'false', 'data-sha': n.sha,
        'aria-label': `${short(n.sha)} ${n.subject}${isHead ? ' (the room now)' : ''}: preview this state`,
        onclick: (e) => { if (e.detail > 1) return; choose(n.sha, e.shiftKey); } },   // a double-click selects once
        el('span', { class: 'g-line1' }, ...refs, el('span', { class: 'g-sha mono', text: short(n.sha) }), el('span', { class: 'g-subject', text: n.subject })),
        el('span', { class: 'g-line2' }, el('span', { text: ago(n.ts) }), changeSummary(n.changed)));
      const chips = el('div', { class: 'g-chips' },
        n.capture_id && el('a', { class: 'g-chip mono', href: `/capture/${encodeURIComponent(n.capture_id)}`, 'data-gate': n.quality_ok === false ? 'reject' : n.quality_ok ? 'pass' : 'unknown' },
          n.capture_id, el('span', { class: 'g-gate', text: n.quality_ok === false ? 'REJECTED' : n.quality_ok ? 'pass' : 'gate not recorded' })),
        ...rejected.map((r) => el('a', { class: 'g-chip g-chip-reject', href: `/capture/${encodeURIComponent(r.capture_id)}`, title: failingText(r) },
          el('span', { text: `${rejected.length === 1 ? '1 capture' : r.capture_id} rejected before this` }), el('span', { class: 'g-gate', text: '→ why?' }))));
      const li = el('li', { class: 'g-row', 'data-sha': n.sha, 'data-new': known.size && !known.has(n.sha) ? '' : null,
        onpointerenter: (e) => { if (e.pointerType === 'mouse') setPeek(n.sha); }, onpointerleave: () => setPeek(null),      // the map ghosts what this commit would change
        onfocusin: () => setPeek(n.sha), onfocusout: () => setPeek(null) }, pick, chips.childNodes.length ? chips : null);
      if (rejected.length) li.dataset.rejected = '';
      if (n.suspect) li.dataset.suspect = '';
      return li;
    }));
    known = new Set(data.nodes.map((n) => n.sha));
  }

  // ---- rails: measured AFTER the rows exist, so wrapped text never misplaces a node -------------
  function drawRails() {
    if (!data) return;
    const { place, edges, width } = layout(data.nodes), W = LANE_W();
    const railW = width * W + 14;
    graph.style.setProperty('--rail', railW + 'px');
    const lis = [...rows.children], y = (row) => lis[row].offsetTop + NODE_Y, x = (lane) => 10 + lane * W + W / 2 - 3;
    rails.setAttribute('width', railW); rails.setAttribute('height', rows.offsetHeight);
    rails.setAttribute('viewBox', `0 0 ${railW} ${rows.offsetHeight}`);
    rails.replaceChildren();
    const K = 30;                                          // how long a lane change takes
    for (const e of edges) {
      const c = place.get(e.child), p = place.get(e.parent);
      if (!p) {                                            // parent is beyond ?limit: the rail runs off the bottom
        rails.append(svg('path', { class: 'g-edge', 'stroke-dasharray': DASH[c.lane % 4], d: `M${x(c.lane)} ${y(c.row)} V${rows.offsetHeight}` }));
        continue;
      }
      let d = `M${x(c.lane)} ${y(c.row)}`, cx = x(c.lane), cy = y(c.row);
      if (e.via !== c.lane) { d += ` C${cx} ${cy + K * 0.6} ${x(e.via)} ${cy + K * 0.4} ${x(e.via)} ${cy + K}`; cx = x(e.via); cy += K; }
      if (e.via !== p.lane) { d += ` V${y(p.row) - K} C${cx} ${y(p.row) - K * 0.4} ${x(p.lane)} ${y(p.row) - K * 0.6} ${x(p.lane)} ${y(p.row)}`; }
      else d += ` V${y(p.row)}`;
      rails.append(svg('path', { class: 'g-edge', 'stroke-dasharray': DASH[e.via % 4], d }));
    }
    data.nodes.forEach((n, row) => {
      const { lane } = place.get(n.sha), cx = x(lane), cy = y(row), g = svg('g', { class: 'g-node', 'data-sha': n.sha });
      if ((n.rejected_before || []).length) {
        // the attempt thrown away on the way INTO this commit: a hazard tick on the rail
        // just below the node (parents are below), and a ring round the node itself
        for (const dy of [17, 23]) g.append(svg('path', { class: 'g-hazard', d: `M${cx - 7} ${cy + dy + 4} L${cx + 7} ${cy + dy - 4}` }));
        g.append(svg('circle', { class: 'g-ring', cx, cy, r: 13.5 }));
      }
      g.append(svg('circle', { class: 'g-dot', cx, cy, r: n.sha === data.head ? 8 : 6.5 }));
      if (n.sha === data.head) g.append(svg('circle', { class: 'g-headdot', cx, cy, r: 3 }));
      if (n.suspect) g.dataset.suspect = '';
      rails.append(g);
    });
    paint();
  }

  function paint() {
    for (const li of rows.children) {
      const sha = li.dataset.sha, on = sha === selected, cmp = sha === compareTo;
      li.toggleAttribute('data-selected', on); li.toggleAttribute('data-compare', cmp);
      li.querySelector('.g-pick').setAttribute('aria-pressed', String(on || cmp));
      const job = jobs.get(sha);
      li.toggleAttribute('data-job', !!job);
    }
    for (const g of rails.querySelectorAll('.g-node')) {
      g.toggleAttribute('data-selected', g.dataset.sha === selected);
      g.toggleAttribute('data-compare', g.dataset.sha === compareTo);
    }
  }

  // ---- selection = PREVIEW, never execution ---------------------------------------------------------
  function choose(sha, shift) {
    if ((compareMode || shift) && selected && sha !== selected) compareTo = sha;
    else { selected = selected === sha && !compareTo ? null : sha; compareTo = null; }
    planShown = null; peek = null;
    paint();
    renderPreview();
    syncMap();
    if (selected && innerWidth < 900) preview.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  function clear() { selected = compareTo = null; planShown = null; paint(); renderPreview(); syncMap(); }
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && selected) { const sha = selected; clear(); rows.querySelector(`[data-sha="${sha}"] .g-pick`)?.focus(); } });

  const opLine = (o, toward) => {
    const cm = typeof o.delta_m === 'number' ? (o.delta_m * 100).toFixed(o.delta_m < 0.1 ? 1 : 0) + ' cm' : null;
    const what = o.op === 'moved' ? (toward ? `would move ${cm || ''}` : `moved ${cm || ''}`) + (o.delta_yaw_deg ? ` · turn ${Math.abs(o.delta_yaw_deg)}°` : '')
        + (o.from_zone && o.from_zone !== o.zone ? ` · ${o.from_zone} → ${o.zone}` : '')
      : o.op === 'removed' ? (toward ? 'would be taken away' : 'removed')
      : o.op === 'added' ? (toward ? 'would be put back — it is not in the room now' : 'added') : 'record changed, object did not move';
    return el('li', { class: 'g-op', 'data-op': o.op },
      el('a', { class: 'mono', href: `/object/${encodeURIComponent(o.object_id)}`, text: o.object_id }),
      el('span', { class: 'g-op-what', text: what.trim() }), el('span', { class: 'g-op-zone mono', text: o.zone ? `zones/${o.zone}` : '' }));
  };

  let renderToken = 0;
  async function renderPreview() {
    clearTimeout(armTimer);
    const token = ++renderToken;
    if (!selected || !data) { hint(); return; }
    const node = data.nodes.find((n) => n.sha === selected), other = compareTo && data.nodes.find((n) => n.sha === compareTo);
    if (!node) { selected = null; hint(); return; }
    const isHead = node.sha === data.head;
    const head = el('div', { class: 'g-p-head' },
      el('span', { class: 'g-p-kicker', text: other ? 'DIFF' : 'PREVIEW' }),
      el('button', { class: 'g-clear', type: 'button', 'aria-label': 'Clear selection (Esc)', text: 'esc ✕', onclick: clear }));
    const title = other
      ? el('p', { class: 'g-p-title' }, el('span', { class: 'mono', text: short(node.sha) }), ' → ', el('span', { class: 'mono', text: short(other.sha) }))
      : el('p', { class: 'g-p-title' }, el('span', { class: 'mono', text: short(node.sha) }), ' ', node.subject);
    const body = el('div', { class: 'g-p-body' }, el('p', { class: 'g-dim', text: 'reading the room’s history…' }));
    preview.replaceChildren(head, title, body);

    // the diagnosis, straight away: what was thrown away before this commit
    const rejected = !other && (node.rejected_before || []).map((r) => el('a', { class: 'g-reject', href: `/capture/${encodeURIComponent(r.capture_id)}` },
      el('strong', { class: 'mono', text: `${r.capture_id} was rejected before this commit` }),
      el('span', { text: `${failingText(r)}. Committed, it would have moved ${plural(r.would_have?.moved ?? 0, 'object')} — the commit that followed moved ${(node.changed?.moved || []).length}.` }),
      el('span', { class: 'g-gate', text: 'open the capture → why?' })));

    const kids = [];
    if (other) {                                           // two moments compared: the object-level diff, as a list
      let diff;
      try { diff = await getJSON(`/api/diff?a=${node.sha}&b=${other.sha}`); }
      catch (e) { if (token === renderToken) body.replaceChildren(el('p', { class: 'g-err', text: `${e.error}: ${e.detail}` })); return; }
      if (token !== renderToken) return;
      const sm = diff.summary, physical = diff.ops.filter((o) => o.op !== 'changed');
      kids.push(el('p', { class: 'g-sum', text: physical.length ? `Between these two moments: ${sm.moved} moved · ${sm.added} added · ${sm.removed} removed.` : 'The room is the same at both.' }));
      if (diff.ops.length) kids.push(el('ul', { class: 'g-ops' }, ...diff.ops.map((o) => opLine(o, false))));
    } else {
      // one commit: the MAP above already shows what would change. Here: the diagnosis, then the command —
      // whose first verb is sent at once (a plan is a read: it goes through the middleware and moves nothing).
      if (rejected.length) kids.push(...rejected);
      const meta = el('p', { class: 'g-meta mono' });
      if (node.capture_id) meta.append(el('a', { href: `/capture/${encodeURIComponent(node.capture_id)}`, text: `capture ${node.capture_id}` }), node.quality_ok === false ? ' · gate REJECTED' : node.quality_ok ? ' · gate passed' : ' · gate not recorded');
      if (node.sentry_trace_id) meta.append(el('span', { class: 'g-trace', title: 'sentry_trace_id — the capture page links the trace when it is a real one', text: `  trace ${node.sentry_trace_id.slice(0, 12)}…` }));
      if (meta.childNodes.length) kids.push(meta);
      kids.push(agentBlock(node, isHead));
    }
    body.replaceChildren(...kids);
  }

  // ---- the command console: every gesture is a COMMAND, sent through the middleware (docs/31) -------------
  // POST /api/agent/command takes text. Andrew's six verbs (restore, status, diff, log, add, commit) are
  // deciphered by HIS parser — live, or a stub that says it is a stub; the graph-native verbs (revert,
  // checkout, cherry-pick) never leave this server. Either way the answer is a PLAN from git reads and
  // nothing has moved. Step two — queueing it for the executor — is a separate, explicit, armed click.
  let bridgeInfo = null, allowList = null, sayNext = null;   // sayNext: the text the next console opens with (the clean-up sentence)
  const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `g-${Date.now()}-${Math.random().toString(16).slice(2)}`);
  const SERVED = { 'andrew:ws': 'Andrew’s agent, live over WebSocket', 'andrew:jsonl': 'Andrew’s parser, live',
    stub: 'STUB — our stand-in for Andrew’s parser, not his code', gitspace: 'this server (graph-native verb: never sent to his middleware)' };

  function verbsFor(node) {
    const sha = short(node.sha), branch = (node.refs || []).find((r) => r.kind === 'branch' && !r.head), onTrunk = (data.trunk || []).includes(node.sha);
    const v = [];
    if (node.sha !== data.head) v.push({ text: `restore ${sha}`, why: 'make the room match this moment — as a NEW commit on top; history keeps the undo' });
    v.push({ text: `revert ${sha}`, why: 'undo only what THIS commit did; everything after it stays' });
    if (branch) v.push({ text: `checkout ${branch.name}`, why: `move HEAD to ${branch.name}: the room becomes that branch` });
    if (!onTrunk) v.push({ text: `cherry-pick ${sha}`, why: 'bring just this commit’s changes into the room as it is now' });
    return v;
  }

  const poseText = (side) => (side && side.pose ? `${side.zone ? `${side.zone} ` : ''}(${['x', 'y', 'z'].map((k) => Number(side.pose[k]).toFixed(2)).join(', ')})` : '—');
  const planLine = (o) => el('li', { class: 'g-op', 'data-id': o.object_id, 'data-op': o.kind === 'move' ? 'moved' : o.kind === 'add' ? 'added' : 'removed',
    onpointerenter: () => map && map.highlight(o.object_id), onpointerleave: () => map && map.highlight(null) },
    el('a', { class: 'mono', href: `/object/${encodeURIComponent(o.object_id)}`, text: o.object_id }),
    el('span', { class: 'g-op-what', text: o.kind === 'move' ? `move ${typeof o.delta_m === 'number' ? `${(o.delta_m * 100).toFixed(0)} cm` : ''} · ${poseText(o.from)} → ${poseText(o.to)}`
      : o.kind === 'add' ? `put back at ${poseText(o.to)}` : `take away from ${poseText(o.from)}` }),
    el('span', { class: 'g-op-zone mono', text: o.kind }));

  // The round trip as a picture (docs/31's `trace`): five stations, lit in order. On the middleware path the
  // command's TEXT goes to Andrew's parser and his intent comes back; a graph verb never leaves this server and
  // his station says so. The station the trip ended at takes the accent; the ones after it stay dark.
  function route(r, failed) {
    const hop = (node) => (r.trace || []).find((t) => t.node === node) || null;
    const panel = hop('panel'), rt = hop('route'), dec = hop('decipher'), ex = hop('executor'), a = r.action || {}, plan = a.result || {};
    const ms = (t) => (t && typeof t.ms === 'number' ? `${t.ms < 10 ? t.ms.toFixed(1) : Math.round(t.ms)} ms` : null);
    const viaAndrew = r.path === 'middleware', last = (r.trace || []).length ? r.trace[r.trace.length - 1].node : null;
    const stations = [
      { k: 'panel', name: 'this graph', line: panel ? `“${panel.label}”` : '—', on: !!panel },
      { k: 'route', name: 'router', line: rt ? `→ ${rt.label}` : '—', title: rt && rt.why, on: !!rt },
      { k: 'decipher', name: viaAndrew ? 'Andrew · gitirl-agent' : 'Andrew · gitirl-agent', who: viaAndrew ? (r.served_by || '') : 'bypassed',
        line: viaAndrew ? (dec ? (r.intent && r.intent.command ? `${r.intent.command}${r.intent.target_state ? ` → ${r.intent.target_state}` : ''}` : dec.label) : 'no answer') : 'graph verb: never sent to him',
        on: viaAndrew && !!dec, skipped: !viaAndrew, time: ms(dec), stub: r.served_by === 'stub' },
      { k: 'executor', name: 'planner · git reads', line: ex ? (ex.error || (a.kind === 'plan' ? `${plural((plan.ops || []).length, 'op')} planned` : a.kind === 'read' ? `read: ${a.as}` : a.kind === 'refused' ? 'refused' : ex.label)) : '—', title: ex && ex.label, on: !!ex, time: ms(ex) },
      { k: 'robot', name: 'executor', line: plan.executor === 'not_connected' || !plan.executor ? 'not connected: nothing moves' : plan.executor, on: false, idle: true },
    ];
    let dead = false;
    return el('ol', { class: 'g-route', 'aria-label': 'Where the command went' }, ...stations.map((st, i) => {
      const isLast = failed && st.k === last, li = el('li', { class: 'g-station', style: `--i:${i}`, title: st.title || null, 'data-on': String(st.on && !dead), 'data-failed': String(isLast), 'data-k': st.k,
        'data-skipped': String(!!st.skipped), 'data-idle': String(!!st.idle) },
        el('span', { class: 'g-st-dot', 'aria-hidden': 'true' }),
        el('span', { class: 'g-st-name' }, st.name, st.who ? el('span', { class: 'g-st-who mono', 'data-stub': String(!!st.stub), text: st.who }) : null),
        el('span', { class: 'g-st-line', text: st.line }), el('span', { class: 'g-st-ms mono', text: st.time || '' }));
      if (isLast) dead = true;
      return li;
    }));
  }

  function agentBlock(node, isHead) {
    const verbs = verbsFor(node), out = el('div', { class: 'g-plan', role: 'status', 'aria-live': 'polite' });
    const opening = sayNext || verbs[0].text; sayNext = null;
    const input = el('input', { class: 'g-line-in mono', type: 'text', value: opening, spellcheck: 'false', autocomplete: 'off', autocapitalize: 'off',
      'aria-label': 'Command to send', 'data-sentry-unmask': true });
    const why = el('p', { class: 'g-verb-why', text: verbs[0].why });
    const armed = el('p', { class: 'g-bridge mono', text: 'asking which decipherer is armed…' });
    const chips = el('div', { class: 'g-verbs', role: 'group', 'aria-label': 'Commands for this commit' }, ...verbs.map((v) =>
      el('button', { type: 'button', class: 'g-verb mono', text: v.text, title: v.why, onclick: () => { input.value = v.text; why.textContent = v.why; send(); } })));
    const go = el('button', { class: 'g-send', type: 'submit', text: 'preview the plan' });
    const form = el('form', { class: 'g-line', onsubmit: (e) => { e.preventDefault(); send(); } }, el('span', { class: 'g-prompt mono', 'aria-hidden': 'true', text: 'room>' }), input, go);

    (async () => {                                       // who will decipher, said BEFORE anything is sent
      try { bridgeInfo = bridgeInfo || await getJSON('/api/agent/bridge'); armed.textContent = `middleware armed: ${bridgeInfo.will_serve} — ${SERVED[bridgeInfo.will_serve] || bridgeInfo.will_serve}`; }
      catch (e) { armed.textContent = e.status === 404 ? 'the middleware endpoint (POST /api/agent/command) is not live on this server' : `middleware: ${e.error || 'unreachable'}`; }
      try { allowList = allowList || await getJSON('/api/commands'); } catch { /* the queue step then explains itself */ }
    })();

    let seq = 0;
    async function send() {
      const text = input.value.trim(), mine = ++seq;
      clearTimeout(armTimer);
      if (!text) return;
      planShown = null; syncMap();                        // the map goes back to the preview until the new plan arrives
      go.disabled = true;
      out.replaceChildren(el('p', { class: 'g-dim', text: `sending “${text}”…` }));
      let r;
      try {
        const res = await fetch('/api/agent/command', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ type: 'user_command', request_id: uuid(), timestamp: new Date().toISOString(), payload: { text } }) });
        r = await res.json();                            // errors keep the same body (docs/31): read it either way
      } catch (e) { r = { error: { code: 'unreachable', message: `this page could not reach its own server (${e.message})` }, trace: [] }; }
      go.disabled = false;
      if (mine !== seq) return;
      const kids = [];
      if (r.path || r.served_by) kids.push(el('p', { class: 'g-served' },
        el('b', { class: 'g-path', 'data-path': r.path || '', text: (r.path || '—').toUpperCase() }), ' deciphered by ',
        el('b', { class: 'mono', 'data-stub': String(r.served_by === 'stub'), text: r.served_by || '—' }), SERVED[r.served_by] ? ` — ${SERVED[r.served_by]}` : ''));
      const failed = r.ok === false || !!r.error;          // docs/31: typed outcomes are HTTP 200 with ok:false — key on the body, never the status
      kids.push(route(r, failed));
      const a = r.action || {}, plan = a.result || {};
      if (failed) {
        const err = r.error || { code: 'failed', message: 'the bridge said ok:false without saying why' }, d = err.details || {};
        kids.push(el('p', { class: 'g-err' }, el('code', { text: err.code }), ` ${err.message || ''}`));
        if (d.hint) kids.push(el('p', { class: 'g-dim', text: d.hint }));
        if ((d.known_states || []).length) kids.push(el('div', { class: 'g-verbs', role: 'group', 'aria-label': 'States the room knows' },    // a wrong name gets the right ones, one click away
          ...d.known_states.slice(0, 12).map((name) => el('button', { type: 'button', class: 'g-verb mono', text: `restore ${name}`, onclick: () => { input.value = `restore ${name}`; send(); } }))));
        kids.push(el('p', { class: 'g-dim', text: 'Nothing was planned and nothing moved. His parser knows six verbs (add, commit, status, diff, restore, log); revert, checkout and cherry-pick are graph verbs.' }));
      }
      else if (a.kind === 'refused') kids.push(el('p', { class: 'g-err', text: plan.detail || `'${a.as}' was refused` }));
      else if (a.kind === 'read') kids.push(el('pre', { class: 'g-read mono', text: JSON.stringify(plan, null, 1).slice(0, 1400) }));
      else if (a.kind === 'plan') {
        const n = (plan.ops || []).length;
        planShown = { ops: plan.ops || [], conflicts: plan.conflicts || [], label: `${a.as} ${plan.ref_resolved || a.ref} — ${plural(n, 'op')}${(plan.conflicts || []).length ? `, ${plural(plan.conflicts.length, 'object')} left alone` : ''}` };
        syncMap();                                         // the plan, drawn: one arrow for a revert, three for a restore
        kids.push(el('p', { class: 'g-safe', text: 'A plan, from git reads only — nothing has moved, room.git was not touched.' }),
          el('p', { class: 'g-sum' }, el('span', { class: 'mono', text: `${a.as} ${a.ref}` }), ` → ${plural(n, 'physical op')}`, n ? `, about ${plan.estimated_s} s of arm time` : ' — the room already matches',
            (plan.conflicts || []).length ? `, ${plural(plan.conflicts.length, 'conflict')} left alone` : '', '.'));
        if (n) kids.push(el('ul', { class: 'g-ops' }, ...plan.ops.map(planLine)));
        if ((plan.conflicts || []).length) kids.push(el('ul', { class: 'g-conflicts' }, ...plan.conflicts.map((c) =>
          el('li', {}, el('a', { class: 'mono', href: `/object/${encodeURIComponent(c.object_id)}`, text: c.object_id }), ` will NOT be touched — ${c.why || 'it no longer applies'}`))));
        if (plan.git_equivalent) kids.push(el('p', { class: 'g-git mono', text: plan.git_equivalent }));
        if (plan.working_tree_dirty) kids.push(el('p', { class: 'g-err', text: 'room.git has uncommitted changes right now — an executor would refuse to start from a dirty tree.' }));
        if (n) kids.push(queueStep(a, plan, node));
      }
      if (r.sentry_trace_id) kids.push(el('p', { class: 'g-meta mono', text: `sentry trace ${r.sentry_trace_id.slice(0, 12)}… — the whole round trip, as spans` }));
      out.replaceChildren(...kids);
    }

    queueMicrotask(send);                                 // selecting a commit previews its first command straight away
    return el('div', { class: 'g-cmd g-agent' },
      el('p', { class: 'g-cmd-what' }, el('b', { text: isHead ? 'This is the room now.' : 'Commands for this commit.' }), ' The graph proposes; the TEXT is what travels — through Andrew’s middleware when it is one of his verbs.'),
      chips, why, form, armed, out);
  }

  // ---- the second, explicit step: hand the previewed plan to the executor's queue ------------------------
  function queueStep(action, plan, node) {
    const verb = action.as, allowed = allowList ? allowList.allowed.includes(verb) : null;
    const out = el('div', { class: 'g-job', role: 'status' });
    const run = el('button', { class: 'g-run', type: 'button', disabled: true, 'data-arming': '', text: `Queue “${verb} ${action.ref}” for the executor` });
    if (allowed === false) {
      run.removeAttribute('data-arming');
      out.replaceChildren(el('span', { text: `'${verb}' is not on this server's allow-list (WEB_ALLOWED_COMMANDS in .env), so it can be previewed but not queued.` }));
      return el('div', { class: 'g-queue' }, run, out);
    }
    // armed only after the plan has been on screen a moment: the second click of a double-click lands on a disabled control
    armTimer = setTimeout(() => { run.disabled = false; run.removeAttribute('data-arming'); }, ARM_MS);
    run.addEventListener('click', async (e) => {
      if (e.detail > 1 || run.disabled) return;           // never on a double-click
      run.disabled = true;
      out.replaceChildren(el('span', { text: 'asking the server…' }));
      try {
        const job = await getJSON('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: verb, args: { ref: action.ref }, base_sha: plan.base_sha }) });   // refused (409) if HEAD moved since the preview
        currentJob = job.job_id; jobs.set(node.sha, job); paint();
        out.replaceChildren(...[
          el('strong', { class: 'mono', text: `${job.job_id} · ${job.state}` }),
          el('span', { text: (job.replayed ? 'This exact plan was already handed over (same HEAD, same target): the same job, not a second one. ' : '')
            + (job.executor === 'not_connected'
              ? `${plural(job.ops.length, 'op')}, about ${job.estimated_s} s of arm time. No executor is connected to this server yet, so nothing moved and room.git was not touched.`
              : `${plural(job.ops.length, 'op')}, about ${job.estimated_s} s.`) }),
          (job.skipped || []).length ? el('span', { text: `${plural(job.skipped.length, 'object')} left alone: ${job.skipped.map((x) => `${x.object_id} (${x.status.replace('_', ' ')})`).join(', ')}.` }) : null,
        ].filter(Boolean));                               // replaceChildren(null) prints the word "null"
      } catch (err) {
        out.replaceChildren(el('span', { class: 'g-err', text: `${err.error}: ${err.detail}` }));
        run.disabled = err.error === 'command_not_allowed';
      }
    });
    return el('div', { class: 'g-queue' }, run, out);
  }

  // ---- load + live ----------------------------------------------------------------------------------------
  async function load() {
    try {
      data = await getJSON('/api/graph?limit=100');
      note.hidden = true;
    } catch (e) {
      note.hidden = false;
      note.textContent = e.status === 404 ? 'GET /api/graph is not live on this server yet.' : `${e.error}: ${e.detail}`;
      return;
    }
    tag.hidden = false;
    tag.textContent = data.source === 'fixture' ? 'git: real · enrichment: fixture data' : data.source === 'git-only' ? 'git only — enrichment unavailable' : 'git + elasticsearch';
    if (selected && !data.nodes.some((n) => n.sha === selected)) selected = compareTo = null;
    renderRows();
    drawRails();
    renderPreview();
    const t = trunkOldestFirst();
    scrub.max = Math.max(0, t.length - 1); scrub.value = scrubbing && t.includes(scrubbing) ? t.indexOf(scrubbing) : scrub.max; scrub.disabled = t.length < 2;
    if (scrubbing && !t.includes(scrubbing)) scrubbing = null;
    paintScrub();
    syncMap().then(warm);
    renderTidy();
  }

  // ---- room clean-up, framed as git ------------------------------------------------------------------------
  const CLEAN_NAMES = ['clean', 'tidy', 'tidied', 'study'];   // the first tag (or branch) with one of these names IS the clean room
  let tidyToken = 0;
  async function renderTidy() {
    const token = ++tidyToken;
    let clean = null;
    for (const name of CLEAN_NAMES) {
      const n = data.nodes.find((x) => (x.refs || []).some((r) => r.name === name && (r.kind === 'tag' || r.kind === 'branch')));
      if (n) { clean = { name, node: n }; break; }
    }
    if (!clean) { tidy.hidden = true; return; }
    let diff;
    try { diff = await getJSON(`/api/diff?a=${data.head}&b=${clean.node.sha}`); } catch { tidy.hidden = true; return; }
    if (token !== tidyToken) return;
    const out = diff.ops.filter((o) => o.op !== 'changed'), names = out.map((o) => (o.class || o.object_id));
    tidy.hidden = false;
    const go = el('button', { class: 'g-tidy-go', type: 'button', text: 'Clean up the room', disabled: !out.length,
      onclick: () => {                                     // selection = a PLAN through the middleware; the robot still needs the second, armed click
        sayNext = `set my room back to ${clean.name} mode`;
        selected = null; choose(clean.node.sha, false);
        preview.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      } });
    tidy.replaceChildren(
      el('div', { class: 'g-tidy-say' },
        el('p', { class: 'g-tidy-k mono', text: 'git status, for a room' }),
        el('p', { class: 'g-tidy-line' }, ...(out.length                 // el() does not flatten: spread
          ? [el('b', { text: `${plural(out.length, 'thing')} out of place` }), ' since the room was last clean (', el('span', { class: 'mono', text: clean.name }), ` · ${short(clean.node.sha)} · ${ago(clean.node.ts)}): ${names.slice(0, 5).join(', ')}${names.length > 5 ? '…' : ''}.`]
          : [el('b', { text: 'The room is clean.' }), ' It matches ', el('span', { class: 'mono', text: clean.name }), ` (${short(clean.node.sha)}).`])),
        el('p', { class: 'g-tidy-sub', text: 'A clean room is a commit. Mess is a diff. Cleaning up is git restore — and every clean-up is a commit you can undo.' })),
      go);
  }

  // A cold /api/state costs about a second (one git read per object); a scrub that stutters is not a scrub.
  // So after the first paint the states a judge is most likely to touch are fetched one at a time, in the
  // background: the trunk (what the scrubber walks), then every other commit on screen (what hovering ghosts).
  let warming = false;
  async function warm() {
    if (warming || !data) return;
    warming = true;
    const order = [...new Set([...(data.trunk || []), ...data.nodes.map((n) => n.sha)])].slice(0, 24);
    for (const sha of order) { try { await stateOf(sha); } catch { /* a commit that cannot be read is simply not warm */ } }
    warming = false;
  }

  function listen() {
    if (!('EventSource' in window)) return;
    const es = window.gitrlEvents || new EventSource('/api/events');
    let timer = 0;
    es.addEventListener('capture', () => { clearTimeout(timer); timer = setTimeout(load, 250); });   // a commit landed: a node appears
    es.addEventListener('job', (ev) => {
      let j; try { j = JSON.parse(ev.data); } catch { return; }
      if (!currentJob || j.id !== currentJob) return;
      const out = preview.querySelector('.g-job strong');
      if (out) out.textContent = `${j.id} · ${j.state}${typeof j.progress === 'number' && j.progress > 0 ? ` · ${Math.round(j.progress * 100)}%` : ''}`;
    });
  }

  new ResizeObserver(() => drawRails()).observe(rows);
  hint();
  load();
  listen();
})();
