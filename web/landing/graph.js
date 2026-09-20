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
// THE GRAPH ITSELF IS NOW cloudline.js. It used to be a vertical rail of dots drawn here —
// one dot per commit, lanes on x, time down the page. A dot is a poor picture of a room, and
// the page ended up with two commit graphs: this one and the point-cloud rail on /robot. So
// the rail is gone and #history's graph is the horizontal filmstrip in cloudline.js, where a
// node IS the room's point cloud at that commit and time runs left to right.
//
// What stays here is everything the rail was a control surface FOR, none of which is a graph:
// the room from above (roommap.js) with its time scrubber, "clean up the room", the preview of
// a commit, the diff between two, and the command console. Those read room.git — object records,
// not clouds — so they keep their own commit picker (a <select>, never a second graph).

(() => {
  const root = document.getElementById('history');
  if (!root) return;

  const ARM_MS = 600;

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

  // (the railroad lane walk moved to cloudline.js, transposed: time on x, lanes on y)

  // ---- state -------------------------------------------------------------------------------
  let data = null, selected = null, compareTo = null, compareMode = false, jobs = new Map();
  let armTimer = 0, currentJob = null;

  const h2 = el('h2', { text: 'Who moved what, and when — the room’s commit graph' });
  const lead = el('p', { class: 'g-lead' },
    'Every change to the shared room is a commit, so nobody has to argue about it. Select one to ', el('strong', { text: 'preview' }),
    ' what putting the room back would take. The roommate moves nothing until you say so.');
  const tag = el('span', { class: 'g-tag', hidden: true });
  const compareBtn = el('button', { class: 'g-toggle', type: 'button', 'aria-pressed': 'false', text: 'compare two commits',
    onclick: () => { compareMode = !compareMode; compareBtn.setAttribute('aria-pressed', String(compareMode)); if (!compareMode) { compareTo = null; renderPreview(); paint(); } hint(); } });
  const tools = el('div', { class: 'g-tools' }, compareBtn, tag);
  // THE GRAPH: the horizontal point-cloud filmstrip, mounted by cloudline.js (imported below)
  const film = el('div', { class: 'g-film' }, el('p', { class: 'g-dim', text: 'reading the room’s point clouds…' }));
  // The room.git commit the panels below are about. A list of commits in a <select> is a picker,
  // not a graph — the page is allowed exactly one graph and it is the filmstrip.
  const picker = el('select', { class: 'g-sel mono', 'aria-label': 'Preview the room at another commit',
    onchange: () => {                                     // this one always names the commit being PREVIEWED
      selected = picker.value || null;
      if (!selected || !compareMode) compareTo = null;
      planShown = null; paint(); renderPreview(); syncMap();
    } });
  const pickerB = el('select', { class: 'g-sel mono', hidden: true, 'aria-label': 'Compare the previewed commit with' });
  pickerB.addEventListener('change', () => { compareTo = pickerB.value || null; planShown = null; paint(); renderPreview(); syncMap(); });
  // ---- the graph rail: lanes, forks and merges, the way `git log --graph` draws them -------------
  // Rows are newest first, so we walk DOWN the page and backwards in time. `open` is one slot per lane,
  // each holding the sha that lane is still waiting to draw. A commit takes the lane that was waiting for
  // it; its first parent inherits that lane, and any second parent (a merge) opens or joins another.
  // Two lanes that end up waiting for the SAME sha have converged — the right-hand one is freed and drawn
  // curving into the left, which is what makes a fork read as a fork rather than two unrelated columns.
  const NSVG = 'http://www.w3.org/2000/svg';
  const LANE_W = 12, DOT_R = 4, LANE_COLOURS = 6;
  const svg = (tag, props) => { const n = document.createElementNS(NSVG, tag);
    for (const [k, v] of Object.entries(props || {})) if (v != null) n.setAttribute(k, v); return n; };

  function layoutLanes(nodes) {
    const open = [];                                  // lane -> the sha that lane is waiting for
    const slot = (sha) => { const i = open.indexOf(sha); if (i >= 0) return i;
      const free = open.indexOf(null); if (free >= 0) { open[free] = sha; return free; }
      open.push(sha); return open.length - 1; };
    return nodes.map((n) => {
      const before = open.slice();
      const lane = slot(n.sha);                       // whoever was waiting for me; else a new lane
      const parents = n.parents || [];
      open[lane] = parents[0] || null;                // the first parent keeps this lane
      const joins = [];                               // extra parents: a merge, drawn as a curve out
      for (const p of parents.slice(1)) joins.push({ to: slot(p), sha: p });
      // anything else still waiting for MY sha was a second child of me: it converges into this dot
      const converge = [];
      for (let i = 0; i < open.length; i++) if (i !== lane && open[i] === n.sha) { converge.push(i); open[i] = null; }
      // and two lanes waiting for the same parent have met: free the right-hand one, curve it left
      for (let i = open.length - 1; i > 0; i--) {
        if (open[i] == null) continue;
        const first = open.indexOf(open[i]);
        if (first >= 0 && first < i) { converge.push({ from: i, into: first }); open[i] = null; }
      }
      while (open.length && open[open.length - 1] == null) open.pop();
      return { sha: n.sha, lane, before, after: open.slice(), joins,
               converge: converge.map((c) => (typeof c === 'number' ? { from: c, into: lane } : c)) };
    });
  }

  // One small SVG per row: the lines that pass straight through, the curves that are born or die here,
  // and this commit's dot. Per-row keeps every row independent of the ones above it, so a re-render of
  // one row cannot smear the rail. 2D SVG only — the page's single WebGL context belongs to the hero.
  function railFor(g, lanesWide, h) {
    const w = Math.max(1, lanesWide) * LANE_W + 6, x = (i) => 6 + i * LANE_W, mid = h / 2;
    const n = svg('svg', { class: 'g-rail', width: w, height: h, viewBox: `0 0 ${w} ${h}`, 'aria-hidden': 'true' });
    const line = (i, from, to, lane) => n.append(svg('path', { class: 'g-rail-line', 'data-lane': lane % LANE_COLOURS,
      d: `M ${x(i)} ${from} L ${x(i)} ${to}` }));
    // lanes that exist above and below this row pass straight through it
    for (let i = 0; i < Math.max(g.before.length, g.after.length); i++) {
      if (i === g.lane) continue;
      const above = g.before[i] != null, below = g.after[i] != null;
      if (above && below) line(i, 0, h, i);
      else if (above) line(i, 0, mid, i);
      else if (below) line(i, mid, h, i);
    }
    // upward only if something ABOVE was already pointing at this lane: a branch tip has nothing above it,
    // and drawing the stub anyway made every tip look like it continued off the top of the list
    if (g.before[g.lane] != null) line(g.lane, 0, mid, g.lane);
    if (g.after[g.lane] != null) line(g.lane, mid, h, g.lane);
    const curve = (fromI, toI, fromY, toY, lane) => n.append(svg('path', { class: 'g-rail-line', 'data-lane': lane % LANE_COLOURS,
      d: `M ${x(fromI)} ${fromY} C ${x(fromI)} ${(fromY + toY) / 2}, ${x(toI)} ${(fromY + toY) / 2}, ${x(toI)} ${toY}` }));
    for (const j of g.joins) curve(g.lane, j.to, mid, h, j.to);        // a merge leaving downward
    // Two shapes, and they start in different places: another lane's child arriving at MY dot comes from the
    // top of the row into the middle; my own lane giving way to one that is already waiting for the same
    // parent leaves FROM the dot and exits at the bottom. Starting both at the top drew a line through the dot.
    for (const c of g.converge) curve(c.from, c.into, c.from === g.lane ? mid : 0, c.into === g.lane ? mid : h, c.from);
    n.append(svg('circle', { class: 'g-rail-dot', 'data-lane': g.lane % LANE_COLOURS, cx: x(g.lane), cy: mid, r: DOT_R }));
    return n;
  }

  // THE COMMIT LIST — the control a git graph is expected to have: click a commit and it is selected.
  // It replaces the <select> as the primary control (the select stays, hidden, so everything that reads
  // picker.value keeps working and compare-with still has a real control). Interaction follows VS Code's
  // source-control graph: one selection, a click never deselects, arrows move it, and the preview follows.
  const commits = el('ul', { class: 'g-commits', role: 'listbox', tabindex: '0',
    'aria-label': 'The room’s commits, newest first — select one to preview it' });
  // The primary picker is hidden (the list above replaced it), so this row is now only the compare-with
  // control: it says so, and it is not on the page at all unless comparing.
  const pickKey = el('span', { class: 'g-pick-k mono', text: 'compare with' });
  const pickRow = el('div', { class: 'g-pickrow', hidden: true }, pickKey, picker, pickerB);
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
  root.append(h2, lead, tools, tidy, film, el('div', { class: 'g-wrap' }, mapWrap, el('div', { class: 'g-side' }, commits, pickRow, preview)), note);
  const map = window.gitrlRoomMap ? window.gitrlRoomMap.create(mapBox) : null;

  // The filmstrip is a separate module and a separate history (the instance repo, which is the
  // only place a commit and a point cloud come out of the same tree). It is loaded lazily so a
  // dashboard on a server without /api/scene still paints everything else.
  import('./cloudline.js').then((m) => m.mount(film)).catch((e) => {
    film.replaceChildren(el('p', { class: 'g-dim', text: `the point-cloud timeline could not load: ${e && e.message}` }));
  });

  // ---- what the map shows: a scrubbed moment > a command's plan > a hovered / selected commit > the room now --------
  const states = new Map();                               // sha -> Promise<state>: commits are immutable
  const stateOf = (sha) => { if (!states.has(sha)) states.set(sha, getJSON(`/api/state?ref=${sha}`).catch((e) => { states.delete(sha); throw e; })); return states.get(sha); };
  const nameOf = (sha) => { const n = data && data.nodes.find((x) => x.sha === sha); return n ? `${short(sha)} · ${n.subject}` : short(sha); };
  let planShown = null, scrubbing = null, mapToken = 0, mapHead = null;
  async function syncMap() {
    if (!map || !data || !data.head) return;
    const token = ++mapToken;
    try {
      const headState = await stateOf(data.head);
      if (token !== mapToken) return;
      if (mapHead !== data.head) { mapHead = data.head; map.setBase(headState); }
      if (scrubbing) { const st = await stateOf(scrubbing); if (token === mapToken) map.state(st, 'THE ROOM THEN', nameOf(scrubbing)); return; }
      if (planShown) { map.plan(planShown.ops, planShown.conflicts, 'THE PLAN', planShown.label); return; }
      const target = compareTo ? null : selected;
      if (target && target !== data.head) {
        const st = await stateOf(target);
        if (token === mapToken) map.diff(st, 'TO MAKE THE ROOM THIS AGAIN', nameOf(target));
        return;
      }
      map.now(`HEAD ${short(data.head)} · ${headState.objects.length} objects`);
    } catch (e) { console.warn('[graph] the room map could not be drawn — the list and the console still work:', e && (e.detail || e.message || e)); }
  }
  if (map) map.onHover = (id) => { for (const li of preview.querySelectorAll('.g-op[data-id]')) li.toggleAttribute('data-hot', !!id && li.dataset.id === id); };

  function trunkOldestFirst() { return data ? [...(data.trunk || [])].reverse() : []; }
  function paintScrub() {
    const t = trunkOldestFirst(), i = scrubbing ? t.indexOf(scrubbing) : t.length - 1, n = data && data.nodes.find((x) => x.sha === t[i]);
    scrubLabel.textContent = !t.length ? '' : scrubbing ? `commit ${i + 1} of ${t.length} · ${short(t[i])} · ${n ? n.subject : ''} · ${n ? ago(n.ts) : ''} — nothing moves` : `${t.length} commits on ${data.branch || 'this branch'} — drag to watch the room change`;
  }
  scrub.addEventListener('input', () => { const t = trunkOldestFirst(), sha = t[Number(scrub.value)]; scrubbing = sha && sha !== data.head ? sha : null; paintScrub(); syncMap(); });

  function hint() {
    if (selected) return;
    preview.replaceChildren(el('div', { class: 'g-empty' },
      el('p', { class: 'g-empty-title', text: compareMode ? 'Pick two commits.' : 'Pick a commit.' }),
      el('p', {}, compareMode ? 'Click one in the list above, then ⌘-click another: the object-level diff between any two moments of the room.'
        : 'Click one in the list above. First you see what that state is and what it would take to get there. Only then can you run it.')));
  }

  // ---- the commit picker -------------------------------------------------------------------------
  function changeSummary(c) {
    if (!c) return '';
    const bits = [['+', c.added], ['~', c.moved], ['−', c.removed]].filter(([, list]) => list && list.length)
      .map(([sym, list]) => `${sym}${list.length}`);
    return bits.length ? `  ${bits.join(' ')}` : '';
  }
  function failingText(r) {
    const f = (r.failing || [])[0];
    return f ? `${f.name} ${Number(f.value).toPrecision(3)} ${f.unit} (limit ${f.limit})` : (r.outcome || 'quality gate');
  }

  // One <option> per commit, newest first, carrying everything the old row carried in words:
  // the refs on it, its short sha, its subject, how long ago, and what it changed.
  function optionText(n) {
    const refs = (n.refs || []).filter((r) => r.kind !== 'remote' && !/^origin(\/|$)/.test(r.name))
      .filter((r) => r.kind !== 'head' || !n.refs.some((o) => o.head && o.kind === 'branch'))
      .map((r) => (r.head && r.kind === 'branch' ? 'HEAD → ' : '') + r.name);
    const marks = [...refs, (n.rejected_before || []).length ? '⚠ a capture was rejected before this' : null].filter(Boolean);
    return `${short(n.sha)}  ${n.subject}${changeSummary(n.changed)}  ·  ${ago(n.ts)}${marks.length ? `  [${marks.join(' · ')}]` : ''}`;
  }
  function fillPicker(sel, blank) {
    const keep = sel.value;
    sel.replaceChildren(el('option', { value: '', text: blank }),
      ...data.nodes.map((n) => el('option', { value: n.sha, text: optionText(n) })));
    sel.value = data.nodes.some((n) => n.sha === keep) ? keep : '';
  }
  function renderPicker() {
    fillPicker(picker, `${data.nodes.length} commits — pick one to preview`);
    fillPicker(pickerB, 'compare with…');
    picker.hidden = true;                    // the list above is the control now; this keeps picker.value honest
    renderCommits();
  }

  // One row per commit, newest first — sha, subject, what it changed, its refs, when. A row is an option
  // in a listbox, so a screen reader gets the same one-selection model the mouse does.
  const ROW_H = 30;                                  // fixed, so a row's rail lines up with the rows above and below
  function renderCommits() {
    if (!data) return;
    const rails = layoutLanes(data.nodes);
    const wide = Math.max(1, ...rails.map((g) => Math.max(g.before.length, g.after.length, g.lane + 1)));
    commits.style.setProperty('--rail-w', `${wide * LANE_W + 6}px`);
    commits.dataset.lanes = String(wide);
    commits.replaceChildren(...data.nodes.map((n, i) => {
      // Which refs earn the space: the ones that name this room's states. A local branch or tag is what a
      // person types; `origin/HEAD` is bookkeeping, so remotes sort last and fall into the "+n" first.
      const rank = (r) => (r.head ? 0 : r.kind === 'branch' ? 1 : r.kind === 'tag' ? 2 : 3);
      const badges = (n.refs || [])
        .filter((r) => r.kind !== 'head' || !n.refs.some((o) => o.head && o.kind === 'branch'))
        .slice().sort((a, b) => rank(a) - rank(b))
        .map((r) => el('span', { class: 'g-badge', 'data-kind': r.kind, 'data-head': r.head ? '' : null,
          title: r.name, text: (r.head && r.kind === 'branch' ? 'HEAD → ' : '') + r.name }));
      // Badges sit INLINE with the subject, the way VS Code draws them, in one flexible middle column. Giving
      // them a grid column of their own let three remote refs take 406 px and squeeze the subject to 4 px.
      const shown = badges.slice(0, 2);
      if (badges.length > shown.length) shown.push(el('span', { class: 'g-badge', 'data-kind': 'more',
        text: `+${badges.length - shown.length}`, title: (n.refs || []).map((r) => r.name).join(', ') }));
      const rail = el('span', { class: 'g-c-rail' });
      rail.append(railFor(rails[i], wide, ROW_H));
      const row = el('li', { class: 'g-commit', role: 'option', 'data-sha': n.sha, id: `g-c-${short(n.sha)}`,
        'aria-selected': 'false', title: n.subject },
        rail,
        el('span', { class: 'g-c-sha mono', text: short(n.sha) }),
        el('span', { class: 'g-c-mid' }, ...shown,
          el('span', { class: 'g-c-subject', text: n.subject }),
          (n.rejected_before || []).length
            ? el('span', { class: 'g-c-warn', title: 'a capture was rejected before this commit', text: '⚠' }) : null),
        el('span', { class: 'g-c-when mono', text: ago(n.ts) }));
      // VS Code: a plain click selects; cmd/ctrl/shift picks the second one to compare against
      row.addEventListener('click', (e) => choose(n.sha, e.shiftKey || e.metaKey || e.ctrlKey));
      return row;
    }));
    paintCommits();
  }

  function paintCommits() {
    let active = null;
    for (const row of commits.children) {
      const sha = row.dataset.sha, on = sha === selected, other = sha === compareTo;
      row.setAttribute('aria-selected', String(on));
      row.toggleAttribute('data-selected', on);
      row.toggleAttribute('data-compare', other);
      if (on) active = row;
    }
    commits.setAttribute('aria-activedescendant', active ? active.id : '');
  }

  // Arrow keys move the selection and the preview follows, as they do in VS Code's graph.
  commits.addEventListener('keydown', (e) => {
    const shas = [...commits.children].map((r) => r.dataset.sha);
    if (!shas.length) return;
    const at = shas.indexOf(selected);
    let next = null;
    if (e.key === 'ArrowDown') next = shas[Math.min(shas.length - 1, at < 0 ? 0 : at + 1)];
    else if (e.key === 'ArrowUp') next = shas[Math.max(0, at < 0 ? 0 : at - 1)];
    else if (e.key === 'Home') next = shas[0];
    else if (e.key === 'End') next = shas[shas.length - 1];
    else return;
    e.preventDefault();
    choose(next, e.shiftKey);
    commits.querySelector('[data-selected]')?.scrollIntoView({ block: 'nearest' });
  });

  function paint() {
    paintCommits();
    picker.value = selected || '';
    pickerB.hidden = !compareMode && !compareTo;
    pickRow.hidden = pickerB.hidden;             // nothing to show when the only live control in it is hidden
    pickerB.value = compareTo || '';
    pickRow.toggleAttribute('data-busy', jobs.size > 0);
  }

  // ---- selection = PREVIEW, never execution ---------------------------------------------------------
  function choose(sha, shift) {
    if ((compareMode || shift) && selected && sha !== selected) compareTo = sha;
    else { selected = sha; compareTo = null; }     // VS Code: clicking the selected commit keeps it selected
    planShown = null;
    paint();
    renderPreview();
    syncMap();
    if (selected && innerWidth < 900) preview.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
  function clear() { selected = compareTo = null; planShown = null; paint(); renderPreview(); syncMap(); }
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && selected) { clear(); commits.focus(); } });

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
  const SERVED = { 'gitspace:grammar': 'parsed here, by the caretaker’s own grammar', 'andrew:intent': 'understood by the language layer',
    'andrew:ws': 'Andrew’s agent, live over WebSocket', 'andrew:jsonl': 'Andrew’s parser, live',
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
    const parsed = r.path === 'middleware' || r.path === 'caretaker', last = (r.trace || []).length ? r.trace[r.trace.length - 1].node : null;
    const hisLayer = /^andrew/.test(r.served_by || ''), sent = !!(plan.dispatch && plan.dispatch.dispatched);
    const stations = [
      { k: 'panel', name: 'this graph', line: panel ? `“${panel.label}”` : '—', on: !!panel },
      { k: 'route', name: 'router', line: rt ? `→ ${rt.label}` : '—', title: rt && rt.why, on: !!rt },
      // WE parse (the caretaker's grammar, or the language layer for what it does not know); a graph verb needs no parsing
      { k: 'decipher', name: hisLayer ? 'language layer' : 'caretaker · parser', who: parsed ? (r.served_by === 'stub' ? 'stand-in' : hisLayer ? 'his' : 'here') : 'not needed',
        line: parsed ? (r.intent && (r.intent.command || r.intent.intent) ? `${r.intent.command || r.intent.intent}${r.intent.target_state ? ` → ${r.intent.target_state}` : r.intent.object ? ` → ${r.intent.object}` : ''}` : (a.as ? `${a.as}${a.ref ? ` → ${a.ref}` : ''}` : (dec ? dec.label : (rt && rt.why) || 'understood'))) : 'a graph verb is already a command',
        on: parsed && (!!dec || !!rt), skipped: !parsed, time: ms(dec), stub: r.served_by === 'stub' },
      { k: 'executor', name: 'planner · git reads', line: ex ? (ex.error || (a.kind === 'plan' ? `${plural((plan.ops || []).length, 'op')} planned` : a.kind === 'read' ? `read: ${a.as}`
        : a.kind === 'job' ? 'a point job' : a.kind === 'jobs' ? `${plural((plan.jobs || []).length, 'move job')}` : a.kind === 'proposal' ? 'needs a pull request' : a.kind === 'refused' ? 'refused' : ex.label)) : '—', title: ex && ex.label, on: !!ex, time: ms(ex) },
      { k: 'robot', name: 'Housebot Edge → robot', line: sent ? `sent · ${plan.dispatch.state || 'dispatching'}` : 'not connected: nothing moves', title: plan.dispatch && plan.dispatch.why, on: sent, idle: !sent },
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
      try { bridgeInfo = bridgeInfo || await getJSON('/api/agent/bridge'); armed.textContent = `parser armed: ${SERVED[bridgeInfo.will_serve] || bridgeInfo.will_serve}`; }
      catch (e) { armed.textContent = e.status === 404 ? 'the middleware endpoint (POST /api/agent/command) is not live on this server' : `middleware: ${e.error || 'unreachable'}`; }
      try { allowList = allowList || await getJSON('/api/commands'); } catch { /* the queue step then explains itself */ }
    })();

    let seq = 0;
    // `extra` carries the bridge's confirm payload (result.yes.payload) through unchanged. The request_id is
    // always fresh: the bridge is idempotent per id, so reusing one replays the QUESTION instead of acting.
    async function send(extra = null, override = null) {
      const text = (override ?? input.value).trim(), mine = ++seq;
      clearTimeout(armTimer);
      if (!text) return;
      planShown = null; syncMap();                        // the map goes back to the preview until the new plan arrives
      go.disabled = true;
      out.replaceChildren(el('p', { class: 'g-dim', text: `sending “${text}”…` }));
      let r;
      try {
        const res = await fetch('/api/agent/command', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({ type: 'user_command', request_id: uuid(), timestamp: new Date().toISOString(), payload: { ...(extra || {}), text } }) });
        r = await res.json();                            // errors keep the same body (docs/31): read it either way
      } catch (e) { r = { error: { code: 'unreachable', message: `this page could not reach its own server (${e.message})` }, trace: [] }; }
      go.disabled = false;
      if (mine !== seq) return;
      const kids = [];
      if (r.path || r.served_by) kids.push(el('p', { class: 'g-served' },          // who understood it, in plain words — never an internal name
        el('b', { class: 'g-path', 'data-path': r.path || '', text: (r.path === 'middleware' ? 'command' : r.path || '—').toUpperCase() }), ' ',
        el('span', { 'data-stub': String(r.served_by === 'stub'), text: SERVED[r.served_by] || 'understood' })));
      const failed = r.ok === false || !!r.error;          // docs/31: typed outcomes are HTTP 200 with ok:false — key on the body, never the status
      kids.push(route(r, failed));
      const a = r.action || {}, plan = a.result || {};
      if (failed) {
        const err = r.error || { code: 'failed', message: 'the bridge said ok:false without saying why' }, d = err.details || {};
        kids.push(el('p', { class: 'g-err' }, el('code', { text: err.code }), ` ${err.message || ''}`));
        if (d.hint) kids.push(el('p', { class: 'g-dim', text: d.hint }));
        if ((d.known_states || []).length) kids.push(el('div', { class: 'g-verbs', role: 'group', 'aria-label': 'States the room knows' },    // a wrong name gets the right ones, one click away
          ...d.known_states.slice(0, 12).map((name) => el('button', { type: 'button', class: 'g-verb mono', text: `restore ${name}`, onclick: () => { input.value = `restore ${name}`; send(); } }))));
        kids.push(el('p', { class: 'g-dim', text: 'Nothing was planned and nothing moved. Try “where are my keys”, “who moved the mug”, “tidy up”, status, log, diff, restore <state> — or a graph verb: revert, checkout, cherry-pick.' }));
      }
      else if (a.kind === 'refused') kids.push(el('p', { class: 'g-err', text: plan.detail || `'${a.as}' was refused` }));
      // A nearest neighbour always exists, so between the refusing floor and the acting floor the bridge ASKS.
      // Nothing is planned or dispatched yet; "yes" is a whole second request, and "no" is never sending it.
      else if (a.kind === 'confirm') {
        const c = plan.candidate || {}, u = plan.runner_up;
        kids.push(el('p', { class: 'g-sum', text: plan.question || 'Did you mean this one?' }));
        const named = (o, lead) => el('p', { class: 'g-dim' }, `${lead} `,
          el('a', { class: 'mono', href: `/object/${encodeURIComponent(o.object_id)}`, text: o.object_id }),
          `${o.class ? ` · ${o.class}` : ''}${o.zone ? ` on the ${o.zone}` : ''}${Number.isFinite(o.score) ? ` · ${o.score.toFixed(3)}` : ''}`);
        if (c.object_id) kids.push(named(c, 'I mean'));
        if (u && u.object_id) kids.push(named(u, 'not'));
        if (plan.why) kids.push(el('p', { class: 'g-dim', text: plan.why }));
        const yes = el('button', { type: 'button', class: 'g-verb mono', text: 'yes, that one' });
        const no = el('button', { type: 'button', class: 'g-verb mono', text: 'no' });
        const row = el('div', { class: 'g-verbs' }, yes, no);
        const settle = (words) => row.replaceChildren(el('span', { class: 'g-dim', text: words }));
        yes.onclick = () => { const y = plan.yes && plan.yes.payload;
          if (!y || !y.text) { settle('that answer carried no request to send'); return; }
          const { text: t, ...rest } = y; settle('yes — sent'); send(rest, t); };
        no.onclick = () => settle(plan.no ? `no — ${plan.no.replace(/^do not send it;\s*/i, '')}` : 'no — nothing was planned or dispatched');
        kids.push(row);
      }
      else if (a.kind === 'read') kids.push(el('pre', { class: 'g-read mono', text: JSON.stringify(plan, null, 1).slice(0, 1400) }));
      else if (a.kind === 'job' && plan.job) {             // "where are my keys": found, and it would go and point
        const jb = plan.job, f = plan.resolved || {};
        kids.push(el('p', { class: 'g-safe', text: plan.dispatch && plan.dispatch.dispatched ? 'Sent to the robot — its answer arrives as the job’s state.' : 'A plan only — no robot is connected, so nothing moved.' }),
          el('p', { class: 'g-sum' }, 'Found ', el('a', { class: 'mono', href: `/object/${encodeURIComponent(jb.object_id)}`, text: jb.object_id }), ` (${f.class || jb.object_id}) on the ${jb.zone || '—'}; it would go over and point at it, about ${jb.estimated_s} s.`));
        planShown = { ops: [], conflicts: [], label: `point at ${f.class || jb.object_id} — ${jb.zone || ''}` }; syncMap().then(() => map && map.highlight(jb.object_id));
      } else if (a.kind === 'jobs') {
        const js = plan.jobs || [];
        kids.push(el('p', { class: 'g-sum', text: js.length ? `Tidy: ${plural(js.length, 'thing')} to put back where ${js.length === 1 ? 'it belongs' : 'they belong'}.` : `${(plan.dispatch && plan.dispatch.why) || 'nothing to tidy'}.` }));
        if (js.length) kids.push(el('ul', { class: 'g-ops' }, ...js.map((x) => el('li', { class: 'g-op', 'data-op': 'moved' }, el('a', { class: 'mono', href: `/object/${encodeURIComponent(x.object_id)}`, text: x.object_id }), el('span', { class: 'g-op-what', text: `put back on the ${x.zone || '—'}` }), el('span', { class: 'g-op-zone mono', text: 'move' })))));
      } else if (a.kind === 'proposal') kids.push(el('p', { class: 'g-sum', text: `That changes where ${(plan.resolved && plan.resolved.class) || 'it'} BELONGS — a decision, not a mess. It goes through a pull request; nothing moves until the household approves it.` }));
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
      el('p', { class: 'g-cmd-what' }, el('b', { text: isHead ? 'This is the room now.' : 'Commands for this commit.' }), ' The graph proposes; the TEXT is what travels — parsed here, planned from git, and run by the robot’s edge when one is connected.'),
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
    renderPicker();
    paint();
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

  hint();
  load();
  listen();
})();
