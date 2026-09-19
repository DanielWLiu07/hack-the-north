// Presentation only: this module reads bounds, never telemetry values or issue state.
export function createScanOverlay() {
  const ns = 'http://www.w3.org/2000/svg';
  const make = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    return el;
  };
  const svg = make('svg', { 'aria-hidden': 'true', 'data-seer-scan': '', focusable: 'false' });
  svg.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:8;overflow:hidden;display:none';
  const fan = make('path', { fill: '#ba7cf5', opacity: '.055' });
  const ray = make('path', { fill: 'none', stroke: '#d7acf9', 'stroke-width': '1.2', opacity: '.65' });
  const glow = make('path', { fill:'none',stroke:'#ba6af4','stroke-width':'12',opacity:'.10' });
  const ribbon = make('path', { fill:'none',stroke:'#d29ef8','stroke-width':'3',opacity:'.5' });
  const core = make('path', { fill:'none',stroke:'#fff','stroke-width':'.8',opacity:'.9' });
  const packets = [0,1,2].map(() => make('circle', { r:'2',fill:'#f5dcff' }));
  const ring = make('circle', { fill: 'none', stroke: '#ef85d8', 'stroke-width': '1' });
  const sparks = make('path', { fill: 'none', stroke: '#ef85d8', 'stroke-width': '1.4', 'stroke-linecap': 'round' });
  svg.append(fan, glow, ribbon, ray, core, ...packets, ring, sparks); document.body.append(svg);
  let last = null;
  function anchor(el) {
    if (!el?.isConnected || !el.getClientRects().length) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.bottom < 24 || r.top > innerHeight - 24 || r.right < 0 || r.left > innerWidth) return null;
    // Aim at the panel margin, leaving its values legible.
    return { x: Math.max(16, Math.min(innerWidth - 16, r.left + Math.min(24, r.width / 2))),
      y: Math.max(16, Math.min(innerHeight - 16, r.top + Math.min(12, r.height / 2))) };
  }
  return {
    anchor,
    pick() {
      return [...document.querySelectorAll('#tiles .tile.bad, #stack .products, #live, #failures .fail, .sentrycol, [data-seer-target]')]
        .find(el => anchor(el)) || null;
    },
    hide() { svg.style.display = 'none'; last = null; },
    draw(source, el, strength, zap, time = 0) {
      const end = anchor(el);
      if (!end || strength < .005 || source.y < 0 || source.y > innerHeight) { this.hide(); return; }
      svg.style.display = 'block'; svg.style.opacity = String(strength);
      const dx = end.x - source.x, dy = end.y - source.y, length = Math.hypot(dx, dy) || 1;
      const nx = -dy / length * 9, ny = dx / length * 9;
      fan.setAttribute('d', `M${source.x},${source.y} L${end.x + nx},${end.y + ny} L${end.x - nx},${end.y - ny} Z`);
      ray.setAttribute('d', `M${source.x},${source.y} L${end.x},${end.y}`);
      const path = ray.getAttribute('d');
      for (const line of [glow,ribbon,core]) line.setAttribute('d',path);
      packets.forEach((packet,i) => { const u=(time*1.5+i/3)%1;
        packet.setAttribute('cx',source.x+dx*u);packet.setAttribute('cy',source.y+dy*u);packet.setAttribute('opacity',Math.sin(u*Math.PI)); });
      ring.setAttribute('cx', end.x); ring.setAttribute('cy', end.y); ring.setAttribute('r', 7 + zap * 12);
      let d = '';
      for (let i = 0; i < 6; i++) {
        const a = i * Math.PI / 3, r = 12 + zap * 14;
        d += `M${end.x + Math.cos(a) * r},${end.y + Math.sin(a) * r} l${Math.cos(a) * 7 * zap},${Math.sin(a) * 7 * zap} `;
      }
      sparks.setAttribute('d', d); sparks.setAttribute('opacity', zap);
      last = { source, end, strength, zap };
    },
    debug: () => last,
    dispose() { svg.remove(); },
  };
}
