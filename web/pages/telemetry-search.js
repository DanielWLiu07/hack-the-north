// Elasticsearch, searching the room's operational record — the panel for /telemetry.
//
// Two halves, because they show different things Elasticsearch is good at:
//   FULL TEXT over room-events.message, with the cluster's own highlighting. 165 events, 158 of
//     them robot_failure, and nothing had ever searched them.
//   ES|QL STATS over ~6.9M telemetry samples, answering in tens of milliseconds.
//
// Every query it runs is printed next to its results. That is the point of the panel: a number
// with the query hidden could have come from anywhere.
//
// NO WebGL. The sparkline is inline SVG with no canvas of any kind — this page already runs three
// WebGL contexts against a budget of one, and the Sentry beat is filmed on it.

const $ = (s, r = document) => r.querySelector(s);
const panel = $('#es-search');
if (panel) boot();

function boot() {
  const form = $('#es-form'), input = $('#es-q'), rows = $('#es-rows'), note = $('#es-note');
  const shown = $('#es-query'), facetRow = $('#es-facets'), stats = $('#es-stats');
  const spark = $('#es-spark'), sparkPick = $('#es-signal'), sparkNote = $('#es-spark-note');
  let facets = null;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // `highlight` is the ONE string rendered as HTML. Elasticsearch produced it with encoder:html,
  // so the message is already escaped and only <mark> is live. Everything else goes through esc().
  const asHtml = (g) => g.highlight || esc(g.message);
  const when = (iso) => (iso ? String(iso).replace('T', ' ').slice(0, 19) + 'Z' : 'not recorded');
  const num = (n) => (typeof n === 'number' ? n.toLocaleString() : 'not recorded');

  async function get(path) {
    const r = await fetch(path, { cache: 'no-store' });
    const body = await r.json().catch(() => null);
    if (!r.ok || !body || body.error) {
      const msg = body?.error?.message || `HTTP ${r.status}`;
      throw new Error(msg);
    }
    return body;
  }

  function showQuery(q) {
    if (!q) { shown.textContent = ''; shown.hidden = true; return; }
    shown.hidden = false;
    shown.textContent = q.kind === 'esql' ? q.query : JSON.stringify(q.query, null, 1);
  }

  function renderRows(data) {
    rows.replaceChildren();
    if (!data.results.length) {
      rows.innerHTML = `<p class="es-empty">No events match. The log holds ${num(data.total)} matching documents.</p>`;
      return;
    }
    for (const g of data.results) {
      const el = document.createElement('article');
      el.className = 'es-hit';
      // count is a RECURRENCE count, not a write count: checked on the real log, every repeated
      // message has distinct timestamps and trace ids. The occurrences are one click away,
      // because a log that hides rows is a log you cannot trust.
      const badge = g.count > 1
        ? `<button class="es-count" type="button" aria-expanded="false">×${g.count}</button>` : '';
      el.innerHTML =
        `<header><span class="es-kind" data-kind="${esc(g.event_type)}">${esc(g.event_type)}</span>` +
        `${badge}<time>${esc(when(g.at))}</time>` +
        `<span class="es-score">score ${Number(g.score ?? 0).toFixed(2)}</span></header>` +
        `<p class="es-msg">${asHtml(g)}</p>` +
        (g.count > 1 ? `<ol class="es-occ" hidden></ol>` : '');
      if (g.count > 1) {
        const list = $('.es-occ', el);
        list.innerHTML = g.occurrences.map((o) =>
          `<li>${esc(when(o.at))}${o.sentry_url
            ? ` · <a href="${esc(o.sentry_url)}" rel="noreferrer noopener" target="_blank">trace</a>` : ''}</li>`).join('');
        $('.es-count', el).addEventListener('click', (e) => {
          const open = list.hasAttribute('hidden');
          list.toggleAttribute('hidden', !open);
          e.currentTarget.setAttribute('aria-expanded', String(open));
        });
      }
      rows.append(el);
    }
  }

  async function run() {
    const params = new URLSearchParams({ q: input.value.trim(), size: '12' });
    for (const sel of facetRow.querySelectorAll('select')) {
      if (sel.value) params.set(sel.name, sel.value);
    }
    note.textContent = 'searching room-events…';
    try {
      const data = await get(`/api/telemetry/search?${params}`);
      renderRows(data);
      showQuery(data.query_shown);
      // `took` is the cluster's, not a stopwatch around the fetch
      note.textContent = `${num(data.total)} matching · ${data.returned} shown · ` +
        `${data.took} ms · ${data.source} · ${data.index}`;
    } catch (e) {
      rows.replaceChildren();
      note.textContent = `Elasticsearch did not answer: ${e.message}`;
    }
  }

  async function loadFacets() {
    try { facets = (await get('/api/telemetry/facets')).facets; } catch { return; }
    facetRow.replaceChildren();
    for (const [name, label] of [['event_type', 'kind'], ['zone', 'zone'], ['branch', 'branch']]) {
      const values = Object.entries(facets[name] || {});
      if (!values.length) continue;
      const sel = document.createElement('select');
      sel.name = name; sel.setAttribute('aria-label', `filter by ${label}`);
      sel.innerHTML = `<option value="">any ${label}</option>` + values
        .map(([v, n]) => `<option value="${esc(v)}">${esc(v)} (${n})</option>`).join('');
      sel.addEventListener('change', run);
      facetRow.append(sel);
    }
  }

  async function loadSignals() {
    try {
      const s = await get('/api/telemetry/signals');
      stats.innerHTML =
        `<p class="es-big">${num(s.total_samples)}<span> samples · ${s.took} ms</span></p>` +
        '<table><thead><tr><th>signal</th><th>samples</th><th>peak |value|</th></tr></thead><tbody>' +
        s.signals.map((x) => `<tr><td>${esc(x.signal)}</td><td>${num(x.samples)}</td>` +
          `<td>${x.peak == null ? 'not recorded' : x.peak.toFixed(4)}</td></tr>`).join('') +
        '</tbody></table>';
      stats.dataset.esql = s.query_shown.query;
      sparkPick.replaceChildren();
      for (const x of s.signals) {
        const o = document.createElement('option');
        o.value = x.signal; o.textContent = x.signal; sparkPick.append(o);
      }
      sparkPick.value = s.signals.some((x) => x.signal === 'tilt_rate') ? 'tilt_rate' : s.signals[0]?.signal;
      drawSpark();
    } catch (e) {
      stats.textContent = `Elasticsearch did not answer: ${e.message}`;
    }
  }

  // Inline SVG. No canvas, no WebGL: this page is already three contexts over its budget.
  async function drawSpark() {
    const signal = sparkPick.value;
    if (!signal) return;
    sparkNote.textContent = 'aggregating…';
    let s;
    try { s = await get(`/api/telemetry/sparkline?signal=${encodeURIComponent(signal)}&buckets=48&span=1%20hour`); }
    catch (e) { sparkNote.textContent = `Elasticsearch did not answer: ${e.message}`; return; }
    const b = s.buckets.filter((x) => x.high != null && x.low != null);
    if (!b.length) { spark.replaceChildren(); sparkNote.textContent = 'no samples in this window'; return; }
    const W = 640, H = 90, lo = Math.min(...b.map((x) => x.low)), hi = Math.max(...b.map((x) => x.high));
    const span = (hi - lo) || 1;
    const x = (i) => (b.length === 1 ? W / 2 : (i / (b.length - 1)) * W);
    const y = (v) => H - ((v - lo) / span) * H;
    const band = b.map((p, i) => `${x(i)},${y(p.high)}`).join(' ') + ' ' +
      b.map((p, i) => `${x(b.length - 1 - i)},${y(b[b.length - 1 - i].low)}`).join(' ');
    spark.setAttribute('viewBox', `0 0 ${W} ${H}`);
    spark.innerHTML =
      `<polygon class="es-band" points="${band}"></polygon>` +
      `<polyline class="es-line" points="${b.map((p, i) => `${x(i)},${y(p.high)}`).join(' ')}"></polyline>`;
    sparkNote.textContent = `${signal} · ${b.length} hourly buckets · ` +
      `${lo.toFixed(3)} to ${hi.toFixed(3)} · ${s.took} ms · ${s.source}`;
    showQuery(s.query_shown);
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); run(); });
  sparkPick.addEventListener('change', drawSpark);
  $('#es-show-esql').addEventListener('click', () => {
    if (stats.dataset.esql) showQuery({ kind: 'esql', query: stats.dataset.esql });
  });
  for (const btn of panel.querySelectorAll('[data-es-example]')) {
    btn.addEventListener('click', () => { input.value = btn.dataset.esExample; run(); });
  }

  loadFacets().then(run);
  loadSignals();
}
