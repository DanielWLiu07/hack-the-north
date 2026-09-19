// roommap.js — the room, from above, for the commit graph (docs/24 Part B: "first click GHOSTS the state").
//
// A commit graph of a room is only a control surface if you can SEE the room. This draws it top-down from
// GET /api/state (zones + every object's pose, extents and yaw, in metres) and lays a command's plan over it:
//
//   solid footprint   where the object is in the room NOW (HEAD)
//   dashed footprint  where it would be
//   arrow             the arm carries it from one to the other        × taken away      + put back
//   the accent        an object the command will NOT touch (a conflict) — the accent only ever means "wrong"
//
// That is what makes `revert` and `restore` different at a glance: one arrow, or three.
// Frame: world Z-up, X forward, Y left (docs/20). Seen from above with the robot's forward UP the screen,
// so +Y is screen-left. Everything is to scale except a minimum footprint of 9 px (a marker is 1 cm wide).
// No library, no canvas: ~30 SVG nodes. Classic script; exposes window.gitrlRoomMap.create(host).

(() => {
  const NS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, ...kids) {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs || {}) if (attrs[k] != null && attrs[k] !== false) n.setAttribute(k, attrs[k]);
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
    return n;
  }
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const still = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const same = (a, b) => a && b && Math.hypot(a.x - b.x, a.y - b.y, (a.z || 0) - (b.z || 0)) < 0.005 && Math.abs((((a.yaw || 0) - (b.yaw || 0) + 540) % 360) - 180) < 0.5;
  const byId = (state) => new Map(((state && state.objects) || []).map((o) => [o.object_id, o]));

  function create(host) {
    host.classList.add('rm');
    const caption = el('p', 'rm-caption'), svg = s('svg', { class: 'rm-svg', role: 'img' }), legend = el('p', 'rm-legend');
    const tip = el('p', 'rm-tip'); tip.setAttribute('aria-live', 'polite');
    host.replaceChildren(caption, svg, tip, legend);
    let base = null, view = { kind: 'now' }, hot = null, raf = 0, onPick = null;

    // ---- what is drawn: [{id, cls, from?, to?, mark}] from a target state or from a plan's ops -----------------
    function changes() {
      const now = byId(base), out = [];
      if (view.kind === 'diff') {
        const then = byId(view.state);
        for (const [id, o] of now) { const t = then.get(id); if (!t) out.push({ id, o, from: o.pose, mark: 'remove' }); else if (!same(o.pose, t.pose)) out.push({ id, o: t, from: o.pose, to: t.pose, mark: 'move' }); }
        for (const [id, t] of then) if (!now.has(id)) out.push({ id, o: t, to: t.pose, mark: 'add' });
      } else if (view.kind === 'plan') {
        for (const op of view.ops || []) {
          const o = now.get(op.object_id) || { object_id: op.object_id, class: op.class, extents: null };
          out.push({ id: op.object_id, o, from: op.from && op.from.pose, to: op.to && op.to.pose, mark: op.kind });
        }
        for (const c of view.conflicts || []) { const o = now.get(c.object_id); out.push({ id: c.object_id, o: o || { object_id: c.object_id }, from: o && o.pose, mark: 'conflict', why: c.why }); }
      }
      return out;
    }

    function render() {
      cancelAnimationFrame(raf);
      const shown = view.kind === 'state' ? view.state : base;
      if (!shown) { svg.replaceChildren(); caption.textContent = 'reading the room…'; return; }
      const ch = view.kind === 'state' ? [] : changes();
      // bounds: every zone, every object drawn, every pose a change mentions
      const pts = [];
      for (const z of Object.values(shown.zones || {})) pts.push([z.min[0], z.min[1]], [z.max[0], z.max[1]]);
      for (const o of shown.objects || []) if (o.pose) pts.push([o.pose.x, o.pose.y]);
      for (const c of ch) for (const p of [c.from, c.to]) if (p) pts.push([p.x, p.y]);
      if (!pts.length) pts.push([0, 0], [1, 1]);
      const m = 0.09, x0 = Math.min(...pts.map((p) => p[0])) - m, x1 = Math.max(...pts.map((p) => p[0])) + m;
      const y0 = Math.min(...pts.map((p) => p[1])) - m, y1 = Math.max(...pts.map((p) => p[1])) + m;
      const W = Math.max(240, host.clientWidth || 480), maxH = W < 420 ? 230 : 380;
      const labelPad = 66;                                // labels hang off an object's RIGHT: keep them inside the frame
      const k = Math.min((W - labelPad) / (y1 - y0), maxH / (x1 - x0)), H = Math.round((x1 - x0) * k), padX = (W - labelPad - (y1 - y0) * k) / 2;
      const X = (y) => padX + (y1 - y) * k, Y = (x) => (x1 - x) * k;         // +Y is screen-left, forward is up
      svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('width', W); svg.setAttribute('height', H);
      const kids = [s('defs', {}, s('marker', { id: 'rm-head', viewBox: '0 0 10 10', refX: 8.5, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, s('path', { d: 'M0 0L10 5L0 10z', class: 'rm-arrowhead' })))];

      for (const [name, z] of Object.entries(shown.zones || {})) {
        kids.push(s('rect', { class: 'rm-zone', x: X(z.max[1]), y: Y(z.max[0]), width: (z.max[1] - z.min[1]) * k, height: (z.max[0] - z.min[0]) * k, rx: 3 }),
          s('text', { class: 'rm-zonelabel', x: X(z.max[1]) + 7, y: Y(z.max[0]) + 15 }, name));
      }
      const step = [0.1, 0.25, 0.5, 1].find((g) => g * k >= 44) || 1;
      kids.push(s('path', { class: 'rm-scale', d: `M10 ${H - 9}h${step * k}` }), s('text', { class: 'rm-scalelabel', x: 10, y: H - 14 }, `${step} m`),
        s('text', { class: 'rm-scalelabel', x: W - 8, y: 14, 'text-anchor': 'end' }, 'robot’s forward ↑'));

      const foot = (o, pose, cls, label) => {
        const ex = (o && o.extents) || { x: 0.06, y: 0.06 }, w = Math.max(ex.y * k, 9), h = Math.max(ex.x * k, 9), cx = X(pose.y), cy = Y(pose.x);
        const g = s('g', { class: `rm-obj ${cls}`, 'data-id': o.object_id, transform: `translate(${cx.toFixed(1)} ${cy.toFixed(1)})` },
          s('rect', { x: -w / 2, y: -h / 2, width: w, height: h, rx: 2, transform: `rotate(${-(pose.yaw || 0)})` }));
        if (label) g.append(s('text', { class: 'rm-label', x: w / 2 + 5, y: 4 }, label));
        g.addEventListener('pointerenter', () => api.highlight(o.object_id, true));
        g.addEventListener('pointerleave', () => api.highlight(null, true));
        g.addEventListener('click', () => onPick && onPick(o.object_id));
        return g;
      };
      const touched = new Map(ch.map((c) => [c.id, c]));
      for (const o of shown.objects || []) {
        if (!o.pose) continue;
        const c = touched.get(o.object_id);
        kids.push(foot(o, o.pose, c ? (c.mark === 'conflict' ? 'rm-conflict' : 'rm-leaving') : 'rm-still', c && c.mark !== 'conflict' ? null : (o.class || o.object_id)));
      }
      const travellers = [];
      for (const c of ch) {
        if (c.mark === 'conflict') { if (c.from) kids.push(s('circle', { class: 'rm-conflictring', cx: X(c.from.y), cy: Y(c.from.x), r: 17 })); continue; }
        if (c.from && c.to) {                             // a move: an arrow, and the ghost at its destination
          const ax = X(c.from.y), ay = Y(c.from.x), bx = X(c.to.y), by = Y(c.to.x), d = Math.hypot(bx - ax, by - ay);
          if (d > 10) kids.push(s('path', { class: 'rm-arrow', d: `M${ax.toFixed(1)} ${ay.toFixed(1)}L${(bx - (bx - ax) / d * 7).toFixed(1)} ${(by - (by - ay) / d * 7).toFixed(1)}`, 'marker-end': 'url(#rm-head)' }));
          else kids.push(s('text', { class: 'rm-turn', x: bx + 9, y: by - 9 }, '↻'));
          const g = foot(c.o, c.to, 'rm-ghost', `${c.o.class || c.id}`); kids.push(g); travellers.push({ g, ax, ay, bx, by });
        } else if (c.to) {
          kids.push(foot(c.o, c.to, 'rm-ghost rm-add', `+ ${c.o.class || c.id}`));
        } else if (c.from) {
          const cx = X(c.from.y), cy = Y(c.from.x);
          kids.push(s('path', { class: 'rm-cross', d: `M${cx - 8} ${cy - 8}L${cx + 8} ${cy + 8}M${cx + 8} ${cy - 8}L${cx - 8} ${cy + 8}` }),
            s('text', { class: 'rm-label rm-gone', x: cx + 12, y: cy + 4 }, `− ${c.o.class || c.id}`));
        }
      }
      svg.replaceChildren(...kids);
      svg.setAttribute('aria-label', `${view.caption || 'The room'} — top-down, ${(shown.objects || []).length} objects${ch.length ? `, ${ch.length} would change` : ''}`);
      caption.replaceChildren(el('b', null, view.kicker || 'THE ROOM NOW'), document.createTextNode(` ${view.caption || ''}`));
      legend.textContent = view.kind === 'state' ? 'The room as it was committed then. Scrubbing moves nothing.'
        : ch.length ? 'solid = where it is now · dashed = where it would be · → the arm carries it · orange = will NOT be touched'
        : view.kind === 'now' ? 'Every object the room holds at HEAD, to scale. Hover a commit to see what would change.' : 'Nothing would move: the room already matches.';
      paintHot();
      if (travellers.length && !still()) {                 // the ghost travels its arrow once, so the eye finds what moves
        const t0 = performance.now(), ms = 820;
        const tick = (now) => {
          const u = Math.min(1, (now - t0) / ms), e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
          for (const t of travellers) t.g.setAttribute('transform', `translate(${(t.ax + (t.bx - t.ax) * e).toFixed(1)} ${(t.ay + (t.by - t.ay) * e).toFixed(1)})`);
          if (u < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      }
    }

    function paintHot() {
      for (const g of svg.querySelectorAll('.rm-obj')) g.classList.toggle('rm-hot', !!hot && g.dataset.id === hot);
      const c = hot && changes().find((x) => x.id === hot), o = hot && (byId(base).get(hot) || (c && c.o));
      tip.textContent = !hot ? '' : `${hot}${o && o.class ? ` · ${o.class}` : ''}${o && o.zone ? ` · ${o.zone}` : ''}${c ? ` — ${c.mark === 'conflict' ? `left alone: ${c.why || 'it no longer applies'}` : c.mark === 'move' ? 'would be moved' : c.mark === 'add' ? 'would be put back' : 'would be taken away'}` : ''}`;
    }

    const api = {
      setBase(state) { base = state; render(); },
      now(caption) { view = { kind: 'now', kicker: 'THE ROOM NOW', caption }; render(); },
      diff(state, kicker, caption) { view = { kind: 'diff', state, kicker, caption }; render(); },
      plan(ops, conflicts, kicker, caption) { view = { kind: 'plan', ops, conflicts, kicker, caption }; render(); },
      state(state, kicker, caption) { view = { kind: 'state', state, kicker, caption }; render(); },
      highlight(id, fromMap) { hot = id; paintHot(); if (fromMap && api.onHover) api.onHover(id); },
      replay() { if (view.kind === 'plan' || view.kind === 'diff') render(); },
      set onPick(fn) { onPick = fn; }, onHover: null,
    };
    svg.addEventListener('click', (e) => { if (e.target === svg) api.replay(); });
    let lastW = 0;                                          // re-draw on a WIDTH change only: our own height change must not loop
    new ResizeObserver(() => { const w = host.clientWidth; if (Math.abs(w - lastW) > 4) { lastW = w; render(); } }).observe(host);
    return api;
  }

  window.gitrlRoomMap = { create };
})();
