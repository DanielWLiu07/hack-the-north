// Presentation scaffold only. No fake sensor data and no robot/splat asset loading.
const canvas = document.querySelector('#spatial'), ctx = canvas.getContext('2d');
const viewer = document.querySelector('.viewer'), reduced = matchMedia('(prefers-reduced-motion: reduce)');
let yaw = -.55, pitch = .58, zoom = 1, targetYaw = yaw, targetPitch = pitch, targetZoom = zoom;
let width = 1, height = 1, raf = 0, last = 0, dragging = null, frames = 0;
let panX=0,panY=0;
const clamp = (v,a,b) => Math.max(a,Math.min(b,v));
function project(x,y,z) {
  const a = x*Math.cos(yaw)-z*Math.sin(yaw), b=x*Math.sin(yaw)+z*Math.cos(yaw);
  const v=y*Math.cos(pitch)-b*Math.sin(pitch), depth=y*Math.sin(pitch)+b*Math.cos(pitch);
  const s=Math.min(width/13,height/8)*zoom*9/(9+depth);
  return [width/2+a*s+panX,height*.53-v*s+panY];
}
function line(a,b,alpha=.2,dash=false){const p=project(...a),q=project(...b);ctx.strokeStyle=`rgba(224,227,232,${alpha})`;ctx.setLineDash(dash?[3,6]:[]);ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();}
function draw(){ctx.clearRect(0,0,width,height);ctx.lineWidth=1;
  if(document.querySelector('#show-grid').checked){
  for(let i=-4;i<=4;i++)line([i,-1.2,-3],[i,-1.2,3],i===0?.25:.085);
  for(let i=-3;i<=3;i++)line([-4,-1.2,i],[4,-1.2,i],i===0?.25:.085);}
  const corners=[[-4,-1.2,-3],[4,-1.2,-3],[4,-1.2,3],[-4,-1.2,3]];
  if(document.querySelector('#show-bounds').checked)corners.forEach((p,i)=>{const q=corners[(i+1)%4],t=[p[0],1.8,p[2]],u=[q[0],1.8,q[2]];line(p,q,.38);line(p,t,.23,true);line(t,u,.25,true);});
  for(const p of corners){const [x,y]=project(...p);ctx.fillStyle='#bcbfc5';ctx.fillRect(x-2,y-2,4,4);}
  // A registration ring, explicitly not a measured robot position.
  for(let i=0;i<64;i++){const a=i/64*Math.PI*2,b=(i+1)/64*Math.PI*2;line([Math.cos(a)*.65,-1.19,Math.sin(a)*.65],[Math.cos(b)*.65,-1.19,Math.sin(b)*.65],.38);}
  frames++;
}
function frame(now){raf=0;if(document.hidden)return;const dt=Math.min((now-last)/1000||.016,.05);last=now;const a=reduced.matches?1:1-Math.exp(-14*dt);yaw+=(targetYaw-yaw)*a;pitch+=(targetPitch-pitch)*a;zoom+=(targetZoom-zoom)*a;draw();if(Math.abs(targetYaw-yaw)+Math.abs(targetPitch-pitch)+Math.abs(targetZoom-zoom)>.0002)wake();}
function wake(){if(!raf&&!document.hidden)raf=requestAnimationFrame(frame);}
new ResizeObserver(()=>{const r=viewer.getBoundingClientRect();width=r.width;height=r.height;const dpr=Math.min(devicePixelRatio,1.5);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);ctx.setTransform(dpr,0,0,dpr,0,0);wake();}).observe(viewer);
function mode(top){document.querySelector('#top').setAttribute('aria-pressed',top);document.querySelector('#perspective').setAttribute('aria-pressed',!top);document.querySelector('#view-label').textContent=top?'Top / illustrative layout':'Perspective / drag to explore';}
canvas.addEventListener('contextmenu',e=>e.preventDefault());
canvas.addEventListener('pointerdown',e=>{dragging=[e.clientX,e.clientY,e.button];canvas.setPointerCapture(e.pointerId);});
canvas.addEventListener('pointermove',e=>{if(!dragging)return;const dx=e.clientX-dragging[0],dy=e.clientY-dragging[1];if(dragging[2]===2||e.shiftKey){panX+=dx;panY+=dy;}else{targetYaw+=dx*.006;targetPitch+=dy*.005;}dragging=[e.clientX,e.clientY,dragging[2]];mode(false);wake();});
canvas.addEventListener('wheel',e=>{e.preventDefault();targetZoom=clamp(targetZoom*Math.exp(-e.deltaY*.001),.0001,10000);wake();},{passive:false});
for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>dragging=null);
canvas.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-'].includes(e.key))return;e.preventDefault();if(e.key==='ArrowLeft')targetYaw-=.15;if(e.key==='ArrowRight')targetYaw+=.15;if(e.key==='ArrowUp')targetPitch+=.12;if(e.key==='ArrowDown')targetPitch-=.12;if(e.key==='+'||e.key==='=')targetZoom=Math.min(targetZoom*1.15,10000);if(e.key==='-')targetZoom=Math.max(targetZoom/1.15,.0001);mode(false);wake();});
document.querySelector('#top').onclick=()=>{targetPitch=1.55;targetYaw=0;mode(true);wake();};
function reset(){targetPitch=.58;targetYaw=-.55;targetZoom=1;panX=0;panY=0;mode(false);wake();}
document.querySelector('#perspective').onclick=reset;document.querySelector('#reset').onclick=reset;
function expand(on){viewer.classList.toggle('expanded',on);document.body.classList.toggle('no-scroll',on);const b=document.querySelector('#expand');b.setAttribute('aria-pressed',on);b.setAttribute('aria-label',on?'Close expanded viewer':'Expand room viewer');}
document.querySelector('#expand').onclick=()=>expand(!viewer.classList.contains('expanded'));
document.addEventListener('keydown',e=>{if(e.key==='Escape')expand(false);});
document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else{last=performance.now();wake();}});
window.roomPreview={get state(){return{yaw,pitch,zoom,frames,pending:!!raf};}};
for(const id of ['show-grid','show-bounds'])document.getElementById(id).onchange=wake;
let searchController=null, results=[];
const status=document.querySelector('#search-status'), output=document.querySelector('#room-results');
function renderResults(){
  output.replaceChildren();const filter=document.querySelector('#match-filter').value;
  const shown=results.filter(r=>filter==='all'||(filter==='vector-only'?r.matched_by?.vector&&!r.matched_by?.bm25:!!r.matched_by?.[filter]));
  for(const r of shown){
    const card=document.createElement('article');card.className='search-result';
    const link=document.createElement('a');link.href=`/object/${encodeURIComponent(r.object_id)}`;link.textContent=r.class||r.object_id;
    const badges=document.createElement('div');badges.className='match-tags';const m=r.matched_by||{};
    badges.textContent=[m.bm25?'KEYWORD':null,m.vector?'SEMANTIC':null,m.rerank_position?`RANK ${m.rerank_position}`:null,r.provenance?.synthetic===false?'CAMERA DESCRIPTION':r.provenance?.synthetic?'SYNTHETIC':'PROVENANCE UNKNOWN'].filter(Boolean).join(' / ');
    const detail=document.createElement('p');detail.textContent=`${r.present_now?'Present at recorded HEAD':'Historical observation'} · ${r.last_seen?.zone||'Zone unavailable'}`;
    const evidence=document.createElement('div');evidence.className='evidence-links';
    const highlight=document.createElement('button');highlight.type='button';highlight.textContent='Show voxels';highlight.onclick=()=>window.dispatchEvent(new CustomEvent('room:select-object',{detail:{objectId:r.object_id,commit:r.last_seen?.commit_sha}}));evidence.append(highlight);
    const cid=r.last_seen?.capture_id;
    if(typeof cid==='string'&&/^[a-z]+_[0-9]+$/.test(cid))for(const [label,path] of [['Capture evidence','capture'],['Motion replay','replay']]){const a=document.createElement('a');a.textContent=label+' ↗';a.href=`/${path}/${encodeURIComponent(cid)}`;evidence.append(a);}
    const seen=document.createElement('p');seen.textContent=r.last_seen?.ts?`Last seen: ${r.last_seen.ts}`:'Last-seen time unavailable';
    card.append(link,badges,detail,seen,evidence);output.append(card);
  }
  if(results.length&&!shown.length){const p=document.createElement('p');p.textContent='No returned results match this filter.';output.append(p);}
}
document.querySelector('#match-filter').onchange=renderResults;
document.querySelector('#room-query').onsubmit=async e=>{
  e.preventDefault();const q=document.querySelector('#room-q').value.trim();if(!q)return;
  const url=new URL(location.href);url.searchParams.set('q',q);url.searchParams.set('all_time',String(document.querySelector('#room-past').checked));history.replaceState(null,'',url);
  searchController?.abort();const ctl=new AbortController();searchController=ctl;const timer=setTimeout(()=>ctl.abort(),15000);results=[];output.replaceChildren();status.textContent='Searching recorded objects…';
  try{const response=await fetch(`/api/search?${new URLSearchParams({q,limit:'20',all_time:String(document.querySelector('#room-past').checked)})}`,{signal:ctl.signal});const data=await response.json();if(!response.ok)throw Error(data.message||data.detail||'Search is unavailable.');if(searchController!==ctl)return;results=Array.isArray(data.results)?data.results:[];const synthetic=data.provenance?.synthetic_results;status.textContent=`${results.length} results. ${synthetic?`${synthetic} use synthetic descriptions. `:data.provenance?'':'Description provenance unavailable. '}${data.reranked===false?'Reranker unavailable. ':''}Show voxels highlights the object's recorded snapshot, where available.`;renderResults();}
  catch(error){if(searchController===ctl)status.textContent=error.name==='AbortError'?'Search timed out. Try again.':`Search unavailable: ${error.message}`;}
  finally{clearTimeout(timer);}
};
const savedQuery=new URLSearchParams(location.search);
document.querySelector('#room-q').value=savedQuery.get('q')||'';
document.querySelector('#room-past').checked=savedQuery.get('all_time')==='true';
// Returning from evidence restores the query, without silently billing a new search.
if(savedQuery.get('q'))status.textContent='Previous query restored. Press Find to refresh its evidence.';
