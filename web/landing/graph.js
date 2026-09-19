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
  root.append(h2, lead, tools, el('div', { class: 'g-wrap' }, graph, preview), note);

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
      const refs = (n.refs || []).filter((r) => r.kind !== 'head' || !n.refs.some((o) => o.head && o.kind === 'branch'))
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
      const li = el('li', { class: 'g-row', 'data-sha': n.sha, 'data-new': known.size && !known.has(n.sha) ? '' : null }, pick, chips.childNodes.length ? chips : null);
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
    paint();
    renderPreview();
    if (selected && innerWidth < 900) preview.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  function clear() { selected = compareTo = null; paint(); renderPreview(); }
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

    let diff;
    try { diff = await getJSON(`/api/diff?a=${other ? node.sha : data.head}&b=${other ? other.sha : node.sha}`); }
    catch (e) { if (token === renderToken) body.replaceChildren(el('p', { class: 'g-err', text: `${e.error}: ${e.detail}` })); return; }
    if (token !== renderToken) return;

    const s = diff.summary, physical = diff.ops.filter((o) => o.op !== 'changed');
    const parts = [s.moved && `${plural(s.moved, 'object')} would move`, s.removed && `${s.removed} would be taken away`, s.added && `${s.added} would be put back`].filter(Boolean);
    const kids = [];
    if (other) {
      kids.push(el('p', { class: 'g-sum', text: physical.length ? `Between these two moments: ${s.moved} moved · ${s.added} added · ${s.removed} removed.` : 'The room is the same at both.' }));
    } else {
      kids.push(el('p', { class: 'g-safe', text: 'Preview only — nothing has moved.' }));
      if (rejected.length) kids.push(...rejected);
      kids.push(el('p', { class: 'g-sum', text: isHead ? 'This is the room as it is committed now.' : parts.length ? `To make the room this again: ${parts.join(', ')}.` : 'The room already matches this state.' }));
    }
    if (diff.ops.length) kids.push(el('ul', { class: 'g-ops' }, ...diff.ops.map((o) => opLine(o, !other))));
    if (!other) {
      const meta = el('p', { class: 'g-meta mono' });
      if (node.capture_id) meta.append(el('a', { href: `/capture/${encodeURIComponent(node.capture_id)}`, text: `capture ${node.capture_id}` }), node.quality_ok === false ? ' · gate REJECTED' : node.quality_ok ? ' · gate passed' : ' · gate not recorded');
      if (node.sentry_trace_id) meta.append(el('span', { class: 'g-trace', title: 'sentry_trace_id — the capture page links the trace when it is a real one', text: `  trace ${node.sentry_trace_id.slice(0, 12)}…` }));
      if (meta.childNodes.length) kids.push(meta);
      if (!isHead && physical.length) kids.push(commandBlock(node, physical.length));
    }
    body.replaceChildren(...kids);
  }

  // ---- the second, explicit step ----------------------------------------------------------------------
  function commandBlock(node, nOps) {
    const branch = (node.refs || []).find((r) => r.kind === 'branch' && !r.head);
    const cmd = branch ? { command: 'checkout', args: { ref: branch.name }, label: `Check out ${branch.name}` }
      : { command: 'revert', args: { ref: node.sha }, label: 'Revert room to here' };
    const out = el('div', { class: 'g-job', role: 'status' });
    const run = el('button', { class: 'g-run', type: 'button', disabled: true, 'data-arming': '', text: cmd.label });
    // armed only after the preview has been on screen a moment: the second click of a
    // double-click on the node lands on a disabled control and does nothing
    armTimer = setTimeout(() => { run.disabled = false; run.removeAttribute('data-arming'); }, ARM_MS);
    run.addEventListener('click', async (e) => {
      if (e.detail > 1 || run.disabled) return;           // never on a double-click
      run.disabled = true;
      out.replaceChildren(el('span', { text: 'asking the server…' }));
      try {
        const job = await getJSON('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ command: cmd.command, args: cmd.args }) });
        currentJob = job.job_id; jobs.set(node.sha, job); paint();
        out.replaceChildren(
          el('strong', { class: 'mono', text: `${job.job_id} · ${job.state}` }),
          el('span', { text: job.executor === 'not_connected'
            ? `Planned ${plural(job.ops.length, 'op')}, about ${job.estimated_s} s of arm time. No robot is connected to this server yet, so nothing moved and room.git was not touched.`
            : `${plural(job.ops.length, 'op')}, about ${job.estimated_s} s.` }));
      } catch (err) {
        out.replaceChildren(el('span', { class: 'g-err', text: `${err.error}: ${err.detail}` }));
        run.disabled = false;
      }
    });
    return el('div', { class: 'g-cmd' },
      el('p', { class: 'g-cmd-what' }, el('span', { class: 'mono', text: `room ${cmd.command} ${cmd.args.ref.length === 40 ? short(cmd.args.ref) : cmd.args.ref}` }), ` — ${plural(nOps, 'physical op')}`),
      run, out);
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
