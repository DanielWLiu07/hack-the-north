// Fictional coloured entrance bugs; never attached to real issue rows.
const shots = [1.75, 2.35, 2.95];
const starts = [.15, .35, .55];
const accents = ['#f757b6','#ffb287','#a596ed'];
export function bugPosition(i, t, width, height) {
  const mobile = width < 760;
  const end = { x: width * (mobile ? [.14,.86,.70] : [.22,.79,.70])[i],
    y: height * (mobile ? [.20,.30,.46] : [.24,.39,.68])[i] };
  const start = [{ x:-48,y:height*.16 },{ x:width+48,y:height*.46 },{ x:width*.7,y:height+48 }][i];
  const u = Math.max(0, Math.min(1, (t - starts[i]) / (shots[i] - starts[i])));
  return { x:start.x+(end.x-start.x)*u + Math.sin(u*Math.PI)*Math.sin(u*8+i)*18,
    y:start.y+(end.y-start.y)*u + Math.sin(u*Math.PI)*Math.cos(u*9+i)*14 };
}
export function createIntroBugs(enabled) {
  if (!enabled) return { target: () => null, draw() {}, hide() {}, dispose() {} };
  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true'); canvas.dataset.seerBugs = '';
  canvas.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:1';
  document.body.append(canvas);
  const ctx = canvas.getContext('2d');
  const point = (i, t) => bugPosition(i, t, innerWidth, innerHeight);
  return {
    target(t) { const i = shots.findIndex(s => t < s + .1); return i < 0 || t < .9 ? null : point(i, Math.min(t, shots[i])); },
    hide() { canvas.style.display = 'none'; },
    draw(t, source, active) {
      if (!active || t > 3.55) { this.hide(); return; }
      canvas.style.display = 'block';
      if (canvas.width !== innerWidth || canvas.height !== innerHeight) { canvas.width = innerWidth; canvas.height = innerHeight; }
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      shots.forEach((shot, i) => {
        const p = point(i, Math.min(t, shot)), age = t - shot;
        if (t < starts[i] || age > .55) return;
        if (age < 0) {
          const prev = point(i, t - .015);
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(Math.atan2(p.y-prev.y,p.x-prev.x)+Math.PI/2);
          ctx.strokeStyle = accents[i]; ctx.fillStyle = '#22172f'; ctx.lineWidth = 2;
          for (const side of [-1, 1]) for (let leg = 0; leg < 3; leg++) {
            const y = -8 + leg * 8, kick = Math.sin(t * 22 + leg * 2) * 3;
            ctx.beginPath(); ctx.moveTo(side * 7, y); ctx.lineTo(side * 15, y - 4 + kick); ctx.lineTo(side * 20, y + 2 + kick); ctx.stroke();
          }
          ctx.beginPath(); ctx.ellipse(0, 2, 9, 14, 0, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
          ctx.beginPath(); ctx.moveTo(0, -9); ctx.lineTo(0, 14); ctx.stroke();
          ctx.fillStyle = '#fff'; ctx.fillRect(-6, -8, 4, 4); ctx.fillRect(2, -8, 4, 4);
          ctx.beginPath(); ctx.moveTo(-4, -11); ctx.lineTo(-9, -20); ctx.moveTo(4, -11); ctx.lineTo(9, -20); ctx.stroke(); ctx.restore();
          if(age>-.24){const charge=1+age/.24;ctx.save();ctx.strokeStyle='#e3b8ff';ctx.globalAlpha=charge*.8;ctx.lineWidth=1.5;
            ctx.beginPath();ctx.arc(source.x,source.y,16-charge*10,-t*9,-t*9+Math.PI*1.6);ctx.stroke();ctx.restore();}
        } else {
          ctx.globalAlpha = Math.max(0, 1 - age / .55);
          ctx.save();ctx.strokeStyle=accents[i];ctx.lineWidth=1.3;
          for(let k=0;k<8;k++){const a=k*Math.PI/4+i*.4,r=10+age*80,length=(1-age/.55)*20;
            ctx.beginPath();ctx.moveTo(p.x+Math.cos(a)*r,p.y+Math.sin(a)*r);
            ctx.lineTo(p.x+Math.cos(a)*(r+length),p.y+Math.sin(a)*(r+length));ctx.stroke();}
          if(age<.12){ctx.translate(p.x,p.y);ctx.rotate(Math.PI/4);ctx.fillStyle='#fff';const size=12*(1-age/.12);ctx.fillRect(-size/2,-size/2,size,size);}
          ctx.restore();
          for (let k = 0; k < 10; k++) {
            const a = k * Math.PI * 2 / 10, r = 8 + age * (80 + k % 3 * 35);
            ctx.fillStyle = k % 2 ? accents[i] : '#6a5fc1';
            ctx.fillRect(p.x + Math.cos(a) * r, p.y + Math.sin(a) * r, 3 + k % 3, 3 + k % 3);
          }
          if (age < .24) {
            const power = 1 - age / .24, dx = p.x-source.x, dy = p.y-source.y, len = Math.hypot(dx,dy)||1;
            ctx.save(); ctx.lineCap = 'round';
            for (const [width, alpha, color] of [[18,.10,accents[i]],[6,.65,accents[i]],[1.8,1,'#ffe4ef']]) {
              ctx.strokeStyle = color; ctx.lineWidth = width * power; ctx.globalAlpha = alpha * power;
              ctx.shadowColor = '#c46bff'; ctx.shadowBlur = width === 6 ? 18 : 0;
              ctx.beginPath(); ctx.moveTo(source.x,source.y);
              for(let j=1;j<=12;j++) { const u=j/12, ripple=Math.sin(u*Math.PI)*Math.sin(j*2+age*65)*3*power;
                ctx.lineTo(source.x+dx*u-dy/len*ripple,source.y+dy*u+dx/len*ripple); }
              ctx.stroke();
            }
            ctx.shadowBlur = 0; ctx.globalAlpha = power; ctx.strokeStyle='#fff'; ctx.lineWidth=1;
            ctx.beginPath();ctx.arc(p.x,p.y,8+age*125,0,Math.PI*2);ctx.stroke();
            ctx.strokeStyle='#d995ff';ctx.beginPath();ctx.arc(source.x,source.y,5+age*40,0,Math.PI*2);ctx.stroke();
            ctx.restore();
          }
          ctx.globalAlpha = 1;
        }
      });
    },
    dispose() { canvas.remove(); },
  };
}
