// room-connections.js — the system, drawn the way the room's own history is drawn: nodes on lanes, branches that
// fork from `main` and merge back. `main` is room.git, the room's truth. A COMMAND branch leaves it (you -> the
// caretaker's parser -> Housebot Edge -> the robot's adapter -> Bracket Bot nav) and merges back as "verified by
// rescan". A PERCEPTION branch (the robot's camera -> the scan pipeline) merges back as a commit. MEMORY hangs off
// main: Elasticsearch and Sentry.
//
// Every node shows its LIVE state, read from this server's own endpoints, and the reason on hover / focus / tap:
//   green  proven reachable just now        amber  configured, or working but stale — NOT proven
//   grey   not wired                        red    wired and failing
// Nothing is ever "connected" without proof. The browser cannot reach the robot's ports itself, so the robot-side
// nodes are only as green as this server's evidence for them. Read-only: this page sends no command.

const NS = 'http://www.w3.org/2000/svg';
const host = document.querySelector('#connection-list'), summary = document.querySelector('#connection-summary'), refresh = document.querySelector('#refresh-connections');
const s = (tag, attrs, ...kids) => { const n = document.createElementNS(NS, tag); for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]); n.append(...kids); return n; };
const h = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
const ago = (iso) => { const sec = (Date.now() - new Date(iso).getTime()) / 1000; if (!isFinite(sec)) return ''; return sec < 90 ? `${Math.round(sec)} s ago` : sec < 5400 ? `${Math.round(sec / 60)} min ago` : sec < 129600 ? `${Math.round(sec / 3600)} h ago` : `${Math.round(sec / 86400)} d ago`; };

async function read(url) {
  try { const r = await fetch(url, { headers: { accept: 'application/json' }, signal: AbortSignal.timeout(7000) }); let body = null; try { body = await r.json(); } catch { /* not json */ } return { status: r.status, ok: r.ok, body: body || {} }; }
  catch (e) { return { status: 0, ok: false, body: {}, down: e.name === 'TimeoutError' ? 'timed out' : 'no answer' }; }
}

// lanes: 0 main · 1 command · 2 perception · 3 memory.  `from` = the node this one forks from / follows; `merge` = lands on main.
const GRAPH = [
  { id: 'main', lane: 0, name: 'main · room.git', sub: 'the room’s truth' },
  { id: 'you', lane: 1, from: 'main', name: 'you · this page', sub: 'a question, or a command' },
  { id: 'parser', lane: 1, from: 'you', name: 'caretaker · parser', sub: 'words → an intent' },
  { id: 'edge', lane: 1, from: 'parser', name: 'Housebot Edge', sub: 'the job, handed to the robot’s side' },
  { id: 'adapter', lane: 1, from: 'edge', name: 'robot adapter', sub: ':8765 · point / move' },
  { id: 'nav', lane: 1, from: 'adapter', name: 'Bracket Bot nav', sub: ':8010 · :8020 · drive, map' },
  { id: 'verified', lane: 0, from: 'main', merge: 'nav', name: 'verified by rescan', sub: 'merge: the room is looked at again' },
  { id: 'camera', lane: 2, from: 'verified', name: 'robot camera', sub: ':8080 · capture' },
  { id: 'scan', lane: 2, from: 'camera', name: 'scan pipeline', sub: 'frames → objects → a diff' },
  { id: 'commit', lane: 0, from: 'verified', merge: 'scan', name: 'commit', sub: 'merge: what was seen becomes history' },
  { id: 'search', lane: 3, from: 'commit', name: 'Elasticsearch', sub: 'search · history', leaf: true },
  { id: 'sentry', lane: 3, from: 'commit', name: 'Sentry', sub: 'traces · the room-clean check', leaf: true },
];
const RANK = { green: 0, amber: 1, grey: 2, red: 3 };
const WORD = { green: 'proven', amber: 'not proven', grey: 'not wired', red: 'failing' };

async function states() {
  const [ci, bridge, edge, nav, cam, health, status, config] = await Promise.all(['/api/room/ci', '/api/agent/bridge', '/api/housebot', '/api/nav/snapshot',
    '/api/robot/view/status', '/api/health', '/api/status', '/api/config'].map(read));
  const st = {}, set = (id, state, why) => { st[id] = { state, why }; };

  const c = ci.body;
  if (!ci.ok) set('main', ci.status === 404 ? 'grey' : 'red', ci.status === 404 ? 'this server has no room-state endpoint mounted' : `the room did not answer (${ci.down || ci.status})`);
  else if (c.state === 'clean') set('main', 'green', `nothing to commit, working tree clean — at ${c.branch || 'main'} · ${c.head || ''}`);
  else if (c.state === 'dirty') set('main', 'amber', `git answers, and the room has drifted: ${new Set((c.changes || []).map((x) => x.object_id || x.path)).size} thing(s) are not where main says`);
  else if (c.state === 'conflict') set('main', 'red', 'a merge conflict: two people moved the same thing');
  else set('main', 'grey', c.detail || 'the room’s state is unknown');

  set('you', 'green', 'you are looking at it');

  const b = bridge.body;
  if (!bridge.ok) set('parser', bridge.status === 404 ? 'grey' : 'red', bridge.status === 404 ? 'the parser is not mounted on this server' : `the parser did not answer (${bridge.down || bridge.status})`);
  else if (b.will_serve === 'stub') set('parser', 'amber', 'a stand-in parser is answering, not the real one');
  else set('parser', 'green', /^andrew/.test(b.will_serve || '') ? 'answering — through the language layer' : 'answering — commands are parsed here, by the caretaker’s own grammar');

  const e = edge.body;
  if (!edge.ok) set('edge', 'grey', edge.status === 404 ? 'the dispatcher is not mounted on this server' : `the dispatcher did not answer (${edge.down || edge.status})`);
  else if (!e.enabled) set('edge', 'grey', 'no edge address is configured, so jobs are planned and never sent');
  else set('edge', 'amber', `configured (${e.edge || 'an edge address'}${e.token ? ', with a token' : ', NO token'}) — no job has proven it reachable yet; sends: ${Object.entries(e.kinds || {}).filter(([, on]) => on).map(([k]) => k).join(', ') || 'nothing allow-listed'}`);

  const n = nav.body;
  if (nav.ok && n.pose && n.stale) { set('nav', 'amber', `it has published its pose; the last one arrived ${Math.round(n.age_s)} s ago, so this is where the robot WAS`); set('adapter', 'amber', 'nothing from the robot’s side has arrived lately'); }
  else if (nav.ok && n.pose) { set('nav', 'green', `publishing its pose and map (${n.status || 'ok'}) · ${typeof n.age_s === 'number' ? `${Math.round(n.age_s)} s ago` : n.at ? ago(n.at) : 'just now'}`); set('adapter', 'amber', 'the nav stack answers; the adapter itself is only proven by a finished job'); }
  else { const why = nav.status === 404 ? 'this server has no nav endpoint mounted' : (n.detail || 'no robot or simulator is publishing its pose to this server'); set('nav', 'grey', why); set('adapter', 'grey', 'nothing from the robot’s side has reached this server'); }
  // only the watch loop can prove this one: a job counts when a CLEAN FRESH pass came after it ended, not when it ended
  const w = c.watch, BLOCKED = { no_registration: 'the robot’s map is not registered to the room yet', slam_not_ready: 'the robot’s map is not ready', map_reset: 'the robot’s map was reset' };
  if (c.last_verified_job) set('verified', 'green', `${c.last_verified_job} was followed by a clean fresh pass — the room was looked at again${w && w.at ? ` · the loop last reported ${ago(w.at)}` : ''}`);
  else if (w && w.blocked) set('verified', 'amber', `the watch loop is running, and its last pass concluded nothing: ${BLOCKED[w.blocked] || w.blocked}`);
  else if (w) set('verified', 'amber', `the watch loop is reporting (${w.passes || 0} fresh pass${w.passes === 1 ? '' : 'es'}); no job has been followed by a clean one yet`);
  else set('verified', 'grey', 'no command has been run and re-scanned yet — a job only counts when the room is looked at again');

  const v = cam.body;
  if (cam.status === 403) set('camera', 'grey', 'the robot’s camera is shown on the robot’s own laptop only');
  else if (!cam.ok) set('camera', 'grey', cam.status === 404 ? 'no camera link is mounted on this server' : `the camera link did not answer (${cam.down || cam.status})`);
  else if (v.frames > 0 && typeof v.frame_age_s === 'number' && v.frame_age_s < 120) set('camera', 'green', `a frame arrived ${Math.round(v.frame_age_s)} s ago from ${v.camera || 'the camera'} (${v.frames} so far)`);
  else if (v.frames > 0) set('camera', 'amber', `it has answered before (${v.frames} frames); the last was ${Math.round(v.frame_age_s || 0)} s ago — ${v.error || 'idle'}`);
  else set('camera', v.robot ? 'red' : 'grey', v.error || (v.robot ? 'configured, and no frame has ever arrived' : 'no robot address is configured'));

  const last = status.body.last_capture || c.last_capture;
  if (!last) set('scan', 'grey', 'no capture has ever been committed');
  else { const mins = (Date.now() - new Date(last).getTime()) / 60000; set('scan', mins < 15 ? 'green' : 'amber', `last scan ${ago(last)}${mins < 15 ? '' : ' — it has worked; nothing proves it is running now'}`); }
  set('commit', c.head ? 'green' : 'grey', c.head ? `HEAD ${c.head} on ${c.branch || 'main'} — git is answering` : 'no commit to show');

  const hl = health.body, es = hl.elastic || {}, se = hl.search || {};
  if (!health.ok && !hl.elastic) set('search', 'red', `this server did not answer (${health.down || health.status})`);
  else if (es.reachable && se.ok !== false) set('search', 'green', `the cluster answered (${es.version || ''} ${es.flavor || ''}) and this server can run the hybrid search`);
  else if (es.reachable) set('search', 'red', se.detail || 'the cluster answers, but this server cannot run the search');
  else set('search', es.configured ? 'red' : 'grey', es.detail || (es.configured ? 'configured, and not answering' : 'not configured'));

  const hb = (c.heartbeat || {}), dsn = ((config.body || {}).sentry || {}).dsn;
  if (hb.at) set('sentry', 'green', `the room-clean check-in was sent ${ago(hb.at)} (${hb.last || 'ok'})`);
  else if (dsn) set('sentry', 'amber', 'configured: events are sent from here, but nothing on this page proves they arrive; no room-clean check-in has been sent yet');
  else set('sentry', 'grey', 'no DSN is configured');
  return st;
}

let chosen = null;
function draw(st) {
  const W = Math.max(260, host.clientWidth || 310), LANE = 19, ROW = 38, X0 = 13, railW = X0 + LANE * 3 + 16, H = GRAPH.length * ROW + 6;
  const x = (lane) => X0 + lane * LANE, y = (i) => 6 + i * ROW + ROW / 2, at = Object.fromEntries(GRAPH.map((g, i) => [g.id, i]));
  const svg = s('svg', { class: 'cx-svg', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'group', 'aria-label': 'How the room’s systems connect, and which are proven right now' });
  const edge = (a, b, state, merge) => {                   // the commit graph's own curve: leave the lane, run, arrive
    const [xa, ya, xb, yb] = [x(GRAPH[a].lane), y(a), x(GRAPH[b].lane), y(b)], K = ROW * 0.62;
    const d = xa === xb ? `M${xa} ${ya}V${yb}` : merge ? `M${xa} ${ya}V${yb - K}C${xa} ${yb - K * 0.4} ${xb} ${yb - K * 0.6} ${xb} ${yb}` : `M${xa} ${ya}C${xa} ${ya + K * 0.6} ${xb} ${ya + K * 0.4} ${xb} ${ya + K}V${yb}`;
    svg.append(s('path', { class: 'cx-edge', 'data-state': state, d }));
  };
  const worst = (...ids) => ids.map((id) => st[id].state).sort((p, q) => RANK[q] - RANK[p])[0];
  let prevMain = null;
  GRAPH.forEach((g, i) => { if (g.lane === 0) { if (prevMain != null) edge(prevMain, i, 'green'); prevMain = i; } });   // main always runs: it is git
  GRAPH.forEach((g, i) => {
    if (g.lane !== 0 && g.from) edge(at[g.from], i, worst(g.id, ...(GRAPH[at[g.from]].lane === 0 ? [] : [g.from])));
    if (g.merge) edge(at[g.merge], i, worst(g.id, g.merge), true);
  });
  GRAPH.forEach((g, i) => {
    const me = st[g.id], row = s('g', { class: 'cx-node', tabindex: 0, role: 'button', 'data-state': me.state, 'data-id': g.id, 'aria-label': `${g.name}: ${WORD[me.state]} — ${me.why}` });
    row.append(s('rect', { class: 'cx-hit', x: 0, y: y(i) - ROW / 2, width: W, height: ROW }),
      g.merge ? s('circle', { class: 'cx-ring', cx: x(g.lane), cy: y(i), r: 10 }) : '',
      s('circle', { class: 'cx-dot', cx: x(g.lane), cy: y(i), r: g.lane === 0 ? 7 : 6 }),
      s('text', { class: 'cx-name', x: railW, y: y(i) - 2 }, g.name), s('text', { class: 'cx-sub', x: railW, y: y(i) + 12 }, g.sub),
      s('text', { class: 'cx-word', x: W - 4, y: y(i) - 2, 'text-anchor': 'end' }, WORD[me.state]));
    const pick = () => { chosen = g.id; say(g, me); for (const n of svg.querySelectorAll('.cx-node')) n.toggleAttribute('data-on', n.dataset.id === g.id); };
    row.addEventListener('pointerenter', pick); row.addEventListener('focus', pick); row.addEventListener('click', pick);
    svg.append(row);
  });
  const detail = h('p', 'cx-detail'); detail.setAttribute('aria-live', 'polite');
  const say = (g, me) => { detail.replaceChildren(h('b', null, `${g.name} — ${WORD[me.state]}. `), document.createTextNode(`${me.why[0].toUpperCase()}${me.why.slice(1)}.`)); detail.dataset.state = me.state; };
  const legend = h('p', 'cx-legend');
  for (const k of ['green', 'amber', 'grey', 'red']) { const i = h('i'); i.dataset.state = k; legend.append(i, document.createTextNode(`${WORD[k]}  `)); }
  host.replaceChildren(svg, detail, legend);
  const first = GRAPH.find((g) => g.id === chosen) || GRAPH.slice().sort((p, q) => RANK[st[q.id].state] - RANK[st[p.id].state])[0];   // open on what most needs saying
  say(first, st[first.id]); svg.querySelector(`[data-id="${first.id}"]`).setAttribute('data-on', '');
}

let last = null, timer = 0;
async function update() {
  refresh.disabled = true; summary.textContent = 'checking…';
  try {
    last = await states(); draw(last);
    const count = (k) => Object.values(last).filter((v) => v.state === k).length;
    summary.textContent = `${count('green')} proven · ${count('amber')} not proven · ${count('grey')} not wired${count('red') ? ` · ${count('red')} failing` : ''}`;
  } catch (e) { summary.textContent = 'could not be read'; host.replaceChildren(h('p', 'cx-detail', `This page could not read its own server: ${e.message}`)); }
  refresh.disabled = false;
  clearTimeout(timer); timer = setTimeout(() => { if (!document.hidden && host.isConnected && host.offsetParent) update(); else timer = setTimeout(update, 15000); }, 15000);
}
refresh.onclick = update;
new ResizeObserver(() => { if (last && Math.abs((host.clientWidth || 0) - (host.dataset.w || 0)) > 12) { host.dataset.w = host.clientWidth; draw(last); } }).observe(host);
update();
