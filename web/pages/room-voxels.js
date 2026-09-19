import * as THREE from 'three';
import { FreeCameraControls as OrbitControls } from './room-camera.js';

const status=document.querySelector('#voxel-status'), load=document.querySelector('#load-voxels');
const clear=document.querySelector('#clear-voxel-selection'), host=document.querySelector('.viewer');
const inspect=document.querySelector('#geohash-inspect'), keyEl=document.querySelector('#geohash-key');
const metaEl=document.querySelector('#geohash-meta'), drillBtn=document.querySelector('#geohash-drill');
const widerBtn=document.querySelector('#geohash-wider');
const stage=document.querySelector('#octree-stage'), queryLine=document.querySelector('#octree-query-line');
const queryMeta=document.querySelector('#octree-query-meta'), labelRoot=document.querySelector('#octree-labels');
const playBtn=document.querySelector('#octree-play');
const hashForm=document.querySelector('#geohash-search'), hashQ=document.querySelector('#geohash-q');
const hashHits=document.querySelector('#geohash-hits'), hashClear=document.querySelector('#geohash-clear');
const toggleBtn=document.querySelector('#octree-toggle');
let renderer,scene,camera,controls,mesh,fill,plates,volume,region,grid,box,group,raf=0,controller,requestId=0,stored=null,selection=null,frameCount=0,topView=false;
let displayed=[],prefix='',level='full',focus=null,hover=null,down=null,prefixTimer=0,pendingFly=null,playGen=0,hooked=false;
let enabled=false;
// ?commit=<40 hex> pins the snapshot. Without it the API falls back to "latest indexed anywhere",
// which is whatever branch committed last -- on a demo machine that was a live-check commit while
// room.git HEAD was on main, so the sha in the page chrome disagreed with the repo.
let pinnedCommit=null;
const ink=new THREE.Color('#0b3331'),teal=new THREE.Color('#2ee6d6'),hot=new THREE.Color('#d9fff8');
const white=new THREE.Color('#f4fffd'),dim=new THREE.Color('#1a2e2c'),pick=new THREE.Color('#ffffff');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const ray=new THREE.Raycaster(),ndc=new THREE.Vector2(),mat=new THREE.Matrix4();
const pos=new THREE.Vector3(),quat=new THREE.Quaternion(),scl=new THREE.Vector3(),proj=new THREE.Vector3();

function overlay(){return window.roomCloud?.scene&&window.roomCloud.camera&&window.roomCloud.canvas?window.roomCloud:null;}
// The octree ladder, coarsest first. A rung IS a prefix length, so the only thing that makes one
// rung different from another is how many digits it keeps; 'full' means the leaf, whatever depth
// the cube is pinned at. Keep the rungs here and nowhere else: they used to be spelled out in six
// places, which is how the page ended up defaulting to the 1 m view (six cubes, 8% fill).
const LADDER=['3','5','6','7','full'],RUNG={'3':3,'5':5,'6':6,'7':7};
function depthOf(lv,cube){return lv==='full'?(cube?.levels??0):RUNG[lv];}
function cellSize(lv,cube){return cube?cube.size_m/2**depthOf(lv,cube):null;}
function step(lv,by){const i=LADDER.indexOf(lv);return LADDER[Math.min(LADDER.length-1,Math.max(0,i+by))]||lv;}
function apiLevel(){return level==='full'?'full':'l'+level;}
function fieldName(){return level==='full'?'voxel_key':'voxel_key_l'+level;}
function metres(size){return size>=0.95?`${size.toFixed(size%1?1:0)} m`:size>=0.09?`${Math.round(size*100)} cm`:`${(size*100).toFixed(2)} cm`;}
function ownersOf(c){return c.owners instanceof Set?c.owners:new Set([...(c.owners||[]),c.object_id].filter(Boolean));}
function world(center,into){into.set(center[0],center[2],-center[1]);return into;}
function kind(){const s=cellSize(level,stored?.cube);const at=s?` (${metres(s)})`:'';
  return level==='full'?`leaf cells${at}`:`L${level} prefixes${at}`;}
function prettyKey(key){return String(key||'').split('').join('·');}
function parseGeohash(q){
  let raw=String(q||'').trim().toLowerCase();
  raw=raw.replace(/^(voxel_key|prefix|geohash)\s*[:=]\s*/,'');
  raw=raw.replace(/[·.\s_*-]/g,'');
  if(!raw)return '';
  if(!/^[0-7]{1,24}$/.test(raw))return null;
  return raw;
}
// The rung that can actually hold a key of this length -- NOT "long enough, call it the leaf".
// Measuring the string was wrong the moment the ladder grew: a 7-digit key is a 6.25 cm REGION
// once the cube is pinned deeper than 7, and calling it 'full' silently asks for the wrong field.
// Only a key as deep as the cube itself is a leaf.
function levelFor(key,cube=stored?.cube){
  const n=key?key.length:0;
  if(cube&&n>=cube.levels)return 'full';
  return LADDER.find(lv=>lv!=='full'&&RUNG[lv]>=n)||'full';
}

// Say the real cell size rather than a hardcoded one. The cube tells us its own depth, so the
// labels stay true if OCTREE_LEVELS ever changes and the leaf stops being 6.25 cm.
function relabelLevels(cube){
  if(!cube)return;
  for(const opt of document.querySelectorAll('#voxel-level option')){
    const lv=opt.value,s=cellSize(lv,cube);
    if(s)opt.textContent=lv==='full'?`Full cells · ${metres(s)}`:`Prefix / L${lv} · ${metres(s)}`;
  }
  for(const btn of document.querySelectorAll('[data-octree-level]')){
    const s=cellSize(btn.dataset.octreeLevel,cube);
    if(s)btn.textContent=btn.dataset.octreeLevel==='full'?`Leaves · ${metres(s)}`:`L${btn.dataset.octreeLevel} · ${metres(s)}`;
  }
}

function render(){raf=0;if(!enabled||document.hidden||!renderer||overlay())return;const moving=controls.update();renderer.render(scene,camera);projectLabels();frameCount++;if(moving)wake();}
function wake(){const cloud=overlay();if(cloud){cloud.wake();return;}if(enabled&&!raf&&!document.hidden)raf=requestAnimationFrame(render);}

function prefixBox(key,cube){
  const lo=[...cube.origin];let size=cube.size_m;
  for(const char of key){size/=2;[2,1,0].forEach((shift,axis)=>lo[axis]+=size*((+char>>shift)&1));}
  return {center:lo.map(v=>v+size/2),size};
}

function init(){
  const cloud=overlay();
  if(cloud){
    if(!group){group=new THREE.Group();group.name='elastic-octree';cloud.scene.add(group);}
    scene=cloud.scene;camera=cloud.camera;
    if(!cloud.canvas.dataset.octreeBound){
      cloud.canvas.dataset.octreeBound='1';
      cloud.canvas.addEventListener('pointerdown',onDown);
      cloud.canvas.addEventListener('pointermove',onMove);
      cloud.canvas.addEventListener('pointerup',onUp);
      cloud.canvas.addEventListener('pointerleave',()=>{hover=null;paint();});
    }
    if(!hooked){cloud.onFrame(projectLabels);hooked=true;}
    return;
  }
  if(renderer)return;
  renderer=new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power'});renderer.setPixelRatio(Math.min(devicePixelRatio,+document.querySelector('#render-quality').value));renderer.setClearColor('#090909',0);
  renderer.domElement.className='voxel-canvas';renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','Elasticsearch octree overlay. Drag to orbit, scroll to zoom. Click a cube to inspect its prefix.');host.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(45,1,.001,1000);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;controls.zoomSpeed=+document.querySelector('#zoom-speed').value;controls.dampingFactor=.12;controls.addEventListener('change',wake);
  renderer.domElement.addEventListener('pointerdown',onDown);
  renderer.domElement.addEventListener('pointermove',onMove);
  renderer.domElement.addEventListener('pointerup',onUp);
  const resize=()=>{renderer.setSize(host.clientWidth,host.clientHeight);camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();if(mesh)fit(topView);wake();};new ResizeObserver(resize).observe(host);resize();
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else wake();});
  reduced.addEventListener('change',()=>{controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;wake();});
  for(const id of ['perspective','reset','top'])document.getElementById(id).addEventListener('click',()=>{if(stored)fit(id==='top');});
  document.querySelector('#show-grid').addEventListener('change',()=>{if(grid)grid.visible=document.querySelector('#show-grid').checked;wake();});
  document.querySelector('#show-bounds').addEventListener('change',()=>{if(box)box.visible=document.querySelector('#show-bounds').checked;wake();});
}

function fit(top=false){
  if(overlay())return;
  topView=top;
  if(!mesh||!displayed.length)return;const bounds=mesh.geometry.boundingBox,center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const radius=Math.max(size.length()/2,.05),distance=radius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(camera.aspect,1)*1.1;
  controls.target.copy(center);camera.up.set(0,1,0);camera.position.copy(center).add(new THREE.Vector3(top?.001:1,top?1:.8,top?0:1).normalize().multiplyScalar(distance));camera.lookAt(center);controls.sync();controls.update();wake();
}

function flyTo(center,size){
  const cloud=overlay();
  const cam=cloud?.camera||camera,ctl=cloud?.controls||controls;
  if(!cam||!ctl?.sync)return;
  world(center,pos);
  const dist=Math.max(size*2.6,.55);
  cam.up.set(0,1,0);
  ctl.target.copy(pos);
  cam.position.copy(pos).add(new THREE.Vector3(dist*.72,dist*.9,dist*.48));
  ctl.sync();
  wake();
}

function tint(c){
  if(focus?.key===c.key)return pick;
  if(hover?.key===c.key)return white;
  if(selection)return c.owners.has(selection)?hot:dim;
  const heat=Math.min(1,Math.log2(1+(c.count||1))/7);
  return ink.clone().lerp(teal,.4+heat*.6);
}

function paintHud(){
  const n=displayed.length,leaves=stored?.total||stored?.cells?.length||n;
  const src=stored?.aggregated?'terms aggregation':stored?.source==='cached_elasticsearch'?'cached snapshot':prefix?'prefix query':'keyword query';
  queryLine.textContent=prefix
    ? `GET room-voxels  ·  ${src}  field=${fieldName()}  prefix=${prettyKey(prefix)}*`
    : `GET room-voxels  ·  ${src}  field=${fieldName()}`;
  queryMeta.textContent=`${n.toLocaleString()} buckets · ${kind()}${prefix?` under ${prettyKey(prefix)}`:''}  ·  ${Number(leaves).toLocaleString()} docs${stored?.commit_sha?`  ·  ${stored.commit_sha.slice(0,8)}`:''}`;
  status.textContent=`Elasticsearch ${src} · ${n.toLocaleString()} occupied ${kind()}${prefix?` under ${prefix}`:''}${stored?.commit_sha?` · ${stored.commit_sha.slice(0,8)}`:''}. ${stored?.truncated?'Partial snapshot. ':''}`;
  for(const btn of document.querySelectorAll('[data-octree-level]'))btn.setAttribute('aria-pressed',String(btn.dataset.octreeLevel===level));
  inspect.hidden=!enabled||(!focus&&!prefix);
  if(focus){
    keyEl.textContent=prettyKey(focus.key);
    const owned=[...focus.owners];
    metaEl.textContent=`${metres(focus.size)} cube · ${Number(focus.count||1).toLocaleString()} indexed cells${owned.length?` · ${owned.slice(0,4).join(', ')}`:''}`;
    drillBtn.hidden=level==='full';
    widerBtn.hidden=!prefix&&level==='3';
  }else if(prefix){
    keyEl.textContent=prettyKey(prefix);
    metaEl.textContent=`${n.toLocaleString()} occupied ${kind()} in this region`;
    drillBtn.hidden=true;widerBtn.hidden=false;
  }
  if(hashQ&&document.activeElement!==hashQ)hashQ.value=prefix?prettyKey(prefix):'';
  if(hashClear)hashClear.hidden=!prefix;
  syncHits();
}

function labeled(){
  if(displayed.length<=6)return displayed;
  const top=[...displayed].sort((a,b)=>b.count-a.count).slice(0,6);
  const extra=[focus,hover].filter(c=>c&&!top.some(t=>t.key===c.key));
  return [...top,...extra];
}

function syncHits(){
  if(!hashHits)return;
  hashHits.replaceChildren();
  if(!enabled){hashHits.hidden=true;return;}
  const items=labeled();
  hashHits.hidden=!items.length;
  for(const c of items){
    const btn=document.createElement('button');
    btn.type='button';
    btn.textContent=`${prettyKey(c.key)} · ${Number(c.count||1).toLocaleString()}`;
    btn.setAttribute('aria-pressed',String(c.key===prefix||c.key===focus?.key));
    btn.addEventListener('click',()=>{playGen++;searchGeohash(c.key);});
    hashHits.append(btn);
  }
}

function syncLabels(){
  if(!labelRoot)return;
  labelRoot.replaceChildren();
  if(!enabled||level==='full'&&displayed.length>36)return;
  for(const c of labeled()){
    const tag=document.createElement('button');
    tag.type='button';tag.className='octree-tag';tag.dataset.key=c.key;
    const title=document.createElement('b');title.textContent=prettyKey(c.key);
    const sub=document.createElement('span');sub.textContent=`${Number(c.count||1).toLocaleString()} docs · ${metres(c.size)}`;
    tag.append(title,sub);
    tag.setAttribute('aria-label',`${c.key} ${Number(c.count||1).toLocaleString()} · ${metres(c.size)}`);
    if(focus?.key===c.key)tag.dataset.focus='true';
    tag.addEventListener('click',e=>{e.stopPropagation();playGen++;if(focus?.key===c.key&&level!=='full')drill(c);else{focus=c;paint();syncLabels();}});
    labelRoot.append(tag);
  }
}

function projectLabels(){
  if(!enabled||!labelRoot?.children.length)return;
  const cam=eventCamera(),el=eventEl();if(!cam||!el)return;
  const canvas=el,cr=canvas.getBoundingClientRect(),hr=host.getBoundingClientRect();
  for(const tag of labelRoot.children){
    const c=displayed.find(d=>d.key===tag.dataset.key);
    if(!c){tag.hidden=true;continue;}
    world(c.center,proj);proj.y+=c.size*.52;proj.project(cam);
    if(proj.z>1||proj.z<-1||Math.abs(proj.x)>1.35||Math.abs(proj.y)>1.35){tag.hidden=true;continue;}
    tag.hidden=false;
    tag.dataset.focus=focus?.key===c.key?'true':'false';
    tag.style.transform=`translate(${(proj.x*.5+.5)*cr.width+(cr.left-hr.left)}px, ${(-proj.y*.5+.5)*cr.height+(cr.top-hr.top)}px) translate(-50%,-120%)`;
  }
}

function paint(){
  const colors=mesh?.geometry.attributes.color;
  displayed.forEach((c,i)=>{const color=tint(c);if(colors)for(let j=0;j<24;j++)colors.setXYZ(i*24+j,color.r,color.g,color.b);if(fill)fill.setColorAt(i,color);if(plates)plates.setColorAt(i,color);});
  if(colors)colors.needsUpdate=true;
  if(fill?.instanceColor)fill.instanceColor.needsUpdate=true;
  if(plates?.instanceColor)plates.instanceColor.needsUpdate=true;
  clear.hidden=!selection;
  paintHud();
  const canvas=overlay()?.canvas||renderer?.domElement;
  if(canvas)canvas.style.cursor=hover?'pointer':'';
  wake();
}

function dispose(obj){
  if(!obj)return;
  (group||scene)?.remove(obj);
  obj.geometry?.dispose();
  const mats=obj.material;if(Array.isArray(mats))mats.forEach(m=>m.dispose());else mats?.dispose?.();
}

function clearMesh(){
  for(const item of [mesh,fill,plates,volume,region,grid,box])dispose(item);
  mesh=fill=plates=volume=region=grid=box=null;
  labelRoot?.replaceChildren();
}

function addBox(center,size,color,opacity){
  const geo=new THREE.EdgesGeometry(new THREE.BoxGeometry(size,size,size));
  const line=new THREE.LineSegments(geo,new THREE.LineBasicMaterial({color,transparent:true,opacity,depthWrite:false}));
  world(center,line.position);line.renderOrder=4;line.frustumCulled=false;
  (group||scene).add(line);return line;
}

function rebuild(){
  level=document.querySelector('#voxel-level').value;prefix=document.querySelector('#voxel-prefix').value;
  clearMesh();
  const groups=new Map(),cube=stored.cube,scope=document.querySelector('#voxel-scope')?.value||'all';
  const keep=c=>{
    if(prefix&&!c.voxel_key?.startsWith(prefix))return false;
    const owned=!!c.object_id||(c.owners&&c.owners.length);
    if(scope==='objects'&&!owned)return false;
    if(scope==='surfaces'&&owned)return false;
    if(scope==='selected'&&(!selection||!(c.object_id===selection||(c.owners||[]).includes(selection))))return false;
    return true;
  };
  if(stored.aggregated){
    for(const c of stored.cells){if(!keep(c))continue;groups.set(c.voxel_key,{center:c.center,size:c.size,key:c.voxel_key,count:c.count||1,density:c.density||0,owners:ownersOf(c)});}
  }else{
    for(const c of stored.cells){
      if(!keep(c))continue;
      const depth=level==='full'?c.voxel_key?.length:Math.max(+level,prefix.length),key=c.voxel_key?.slice(0,depth)||String(groups.size);
      if(!groups.has(key)){
        let center=c.center,size=c.size;
        if(cube&&key.length<c.voxel_key.length){const lo=[...cube.origin];size=cube.size_m;for(const char of key){size/=2;[2,1,0].forEach((shift,axis)=>lo[axis]+=size*((+char>>shift)&1));}center=lo.map(v=>v+size/2);}
        groups.set(key,{center,size,key,count:0,density:0,owners:new Set()});
      }
      const g=groups.get(key);g.count+=c.count||1;g.density+=c.density||0;if(c.object_id)g.owners.add(c.object_id);
    }
  }
  displayed=[...groups.values()];
  const parent=group||scene;
  if(cube)volume=addBox([cube.origin[0]+cube.size_m/2,cube.origin[1]+cube.size_m/2,cube.origin[2]+cube.size_m/2],cube.size_m,0x145e59,.35);
  if(prefix&&cube){const boxp=prefixBox(prefix,cube);region=addBox(boxp.center,boxp.size,0x2ee6d6,.9);}
  if(!displayed.length){paintHud();return;}
  const unit=new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),edge=unit.attributes.position,positions=new Float32Array(displayed.length*72),colors=new Float32Array(displayed.length*72);
  displayed.forEach((c,i)=>{for(let j=0;j<24;j++){const k=i*72+j*3;positions[k]=c.center[0]+edge.getX(j)*c.size;positions[k+1]=c.center[2]+edge.getY(j)*c.size;positions[k+2]=-c.center[1]+edge.getZ(j)*c.size;}});unit.dispose();
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));geometry.computeBoundingBox();
  const edgeOpacity=+document.querySelector('#voxel-opacity').value;
  mesh=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:edgeOpacity,depthWrite:false}));mesh.renderOrder=3;mesh.frustumCulled=false;parent.add(mesh);
  if(displayed.length<=2500){
    const fillOp=level==='full'?0.06:level==='5'?0.22:0.32;
    fill=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({transparent:true,opacity:fillOp,depthWrite:false}),displayed.length);
    fill.frustumCulled=false;fill.renderOrder=2;
    const plateGeo=new THREE.PlaneGeometry(1,1);plateGeo.rotateX(-Math.PI/2);
    plates=new THREE.InstancedMesh(plateGeo,new THREE.MeshBasicMaterial({transparent:true,opacity:.42,depthWrite:false,depthTest:true}),displayed.length);
    plates.frustumCulled=false;plates.renderOrder=1;
    displayed.forEach((c,i)=>{
      world(c.center,pos);scl.set(c.size,c.size,c.size);mat.compose(pos,quat.identity(),scl);fill.setMatrixAt(i,mat);
      pos.y=c.center[2]-c.size/2+0.004;scl.set(c.size*.98,1,c.size*.98);mat.compose(pos,quat.identity(),scl);plates.setMatrixAt(i,mat);
    });
    fill.instanceMatrix.needsUpdate=true;plates.instanceMatrix.needsUpdate=true;parent.add(fill,plates);
  }
  if(!overlay()&&displayed.length){
    const b=geometry.boundingBox,size=b.getSize(new THREE.Vector3()),center=b.getCenter(new THREE.Vector3());
    grid=new THREE.GridHelper(Math.max(size.x,size.z,.1)*1.2,16,0x34383e,0x171a1f);grid.position.set(center.x,b.min.y,center.z);grid.visible=document.querySelector('#show-grid').checked;scene.add(grid);
    box=new THREE.Box3Helper(b,0x50555c);box.visible=document.querySelector('#show-bounds').checked;scene.add(box);
  }
  if(focus&&!displayed.some(c=>c.key===focus.key))focus=displayed.find(c=>c.key.startsWith(focus.key))||null;
  syncLabels();paint();
  if(pendingFly){
    const cell=displayed.find(c=>c.key===pendingFly)||displayed.find(c=>c.key.startsWith(pendingFly));
    if(cell)flyTo(cell.center,cell.size);
    else if(stored?.cube){const boxp=prefixBox(pendingFly,stored.cube);flyTo(boxp.center,boxp.size);}
    pendingFly=null;
  }
}

function mount(data){
  if(!Array.isArray(data.cells)||!data.cells.length)throw Error('No stored octree cells for this snapshot.');
  const cells=data.cells.filter(c=>Array.isArray(c.center)&&c.center.length===3&&c.center.every(Number.isFinite)&&Number.isFinite(c.size)&&c.size>0).slice(0,20000);
  if(!cells.length)throw Error('No valid octree geometry returned.');
  init();stored={...data,cells};relabelLevels(stored.cube);rebuild();
  if(!overlay()){host.classList.add('voxel-active');fit();}
}

async function fetchJSON(url,signal){const r=await fetch(url,{signal});if(!r.ok){const error=new Error(`Voxel endpoint HTTP ${r.status}`);error.status=r.status;throw error;}return r.json();}

async function getVoxels(commit=pinnedCommit){
  controller?.abort();controller=new AbortController();const ctl=controller,id=++requestId,timer=setTimeout(()=>ctl.abort(),15000);load.disabled=true;status.textContent='Loading Elasticsearch octree…';
  if(queryLine)queryLine.textContent='searching room-voxels…';
  try{
    let data;
    const params={limit:apiLevel()==='full'?'20000':'4000'};
    if(commit)params.commit_sha=commit;
    if(apiLevel()!=='full')params.level=apiLevel();
    if(prefix)params.prefix=prefix;
    try{data=await fetchJSON(`/api/voxels?${new URLSearchParams(params)}`,ctl.signal);}
    catch(e){if(e.status!==404)throw e;data=await fetchJSON('/models/room-voxels-snapshot.json',ctl.signal);if(commit&&data.commit_sha!==commit)throw Error('Requested snapshot is not in the cache. Live voxel API requires activation.');}
    if(id!==requestId||!enabled)return;mount(data);load.textContent='Refresh octree';
  }catch(e){if(id===requestId){status.textContent=`${stored?'Previous snapshot remains visible. ':''}${e.name==='AbortError'?'Octree request timed out.':e.message}`;if(queryMeta)queryMeta.textContent=status.textContent;}}
  finally{clearTimeout(timer);if(id===requestId)load.disabled=false;}
}

function eventCamera(){return overlay()?.camera||camera;}
function eventEl(){return overlay()?.canvas||renderer?.domElement;}
function hitAt(e){
  if(!fill||!enabled)return null;
  const el=eventEl(),cam=eventCamera();if(!el||!cam)return null;
  const r=el.getBoundingClientRect();
  ndc.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);
  ray.setFromCamera(ndc,cam);
  const hits=ray.intersectObject(fill);
  return hits.length?displayed[hits[0].instanceId]||null:null;
}
function onDown(e){if(!enabled)return;down=[e.clientX,e.clientY];playGen++;}
function onMove(e){
  if(!enabled||!fill)return;
  const next=hitAt(e);
  if(next?.key!==hover?.key){hover=next;paint();}
}
function onUp(e){
  if(!enabled||!down)return;
  const dx=e.clientX-down[0],dy=e.clientY-down[1];down=null;
  if(dx*dx+dy*dy>25)return;
  const cell=hitAt(e);
  if(cell&&focus?.key===cell.key&&level!=='full'){drill(cell);return;}
  focus=cell;paint();syncLabels();
}

function setLevel(next,nextPrefix){
  document.querySelector('#voxel-level').value=next;
  document.querySelector('#voxel-prefix').value=nextPrefix;
  level=next;prefix=nextPrefix;
  if(hashQ&&document.activeElement!==hashQ)hashQ.value=nextPrefix?prettyKey(nextPrefix):'';
  document.querySelector('#voxel-level').dispatchEvent(new Event('change'));
}

function searchGeohash(q){
  const key=parseGeohash(q);
  if(key===null){
    if(queryMeta)queryMeta.textContent='Not an octree geohash. Digits 0–7 only — a prefix is a region.';
    hashQ?.setCustomValidity('Digits 0–7 only');
    hashQ?.reportValidity?.();
    return;
  }
  hashQ?.setCustomValidity('');
  if(!enabled)setEnabled(true);
  pendingFly=key||null;
  focus=key?displayed.find(c=>c.key===key)||null:null;
  setLevel(levelFor(key),key);
}

function drill(cell){
  if(!cell||level==='full')return;
  pendingFly=cell.key;
  setLevel(step(level,1),cell.key);
}

drillBtn?.addEventListener('click',()=>{playGen++;if(focus)drill(focus);});
widerBtn?.addEventListener('click',()=>{
  playGen++;
  if(prefix){const next=prefix.slice(0,-1);pendingFly=next||null;setLevel(levelFor(next),next);}
  else if(level!==LADDER[0])setLevel(step(level,-1),'');
});

for(const btn of document.querySelectorAll('[data-octree-level]')){
  btn.addEventListener('click',()=>{playGen++;setLevel(btn.dataset.octreeLevel,prefix);});
}

async function playPrefix(){
  const id=++playGen;
  const wait=ms=>new Promise(r=>setTimeout(r,reduced.matches?0:ms));
  const ready=()=>new Promise(resolve=>{
    const t=setInterval(()=>{if(id!==playGen){clearInterval(t);resolve(false);}if(stored&&!load.disabled){clearInterval(t);resolve(true);}},40);
  });
  if(playBtn){playBtn.disabled=true;playBtn.textContent='Playing…';}
  try{
    if(!enabled)setEnabled(true);
    setLevel('3','');
    if(!(await ready())||id!==playGen)return;
    const densest=[...displayed].sort((a,b)=>b.count-a.count)[0];
    if(!densest)return;
    focus=densest;paint();syncLabels();flyTo(densest.center,densest.size);
    await wait(1400);if(id!==playGen)return;
    drill(densest);
    await wait(1800);if(id!==playGen)return;
    const finer=[...displayed].sort((a,b)=>b.count-a.count)[0];
    if(finer&&level!=='full')drill(finer);
  }finally{
    if(id===playGen&&playBtn){playBtn.disabled=false;playBtn.textContent='Play prefix';}
  }
}
playBtn?.addEventListener('click',()=>playPrefix());

load.onclick=()=>{if(!enabled)setEnabled(true);else getVoxels(stored?.commit_sha);};
clear.onclick=()=>{selection=null;if(stored)rebuild();};
function setEnabled(on){
  enabled=on;document.querySelector('#enable-voxels').checked=on;document.body.classList.toggle('voxels-enabled',on);
  toggleBtn?.setAttribute('aria-pressed',String(on));
  if(stage)stage.hidden=!on;
  if(group)group.visible=on;
  if(renderer)renderer.domElement.hidden=!on;
  if(!overlay())host.classList.toggle('voxel-active',on&&!!stored);
  inspect.hidden=!on||(!focus&&!prefix);
  if(!on){controller?.abort();requestId++;load.disabled=false;cancelAnimationFrame(raf);raf=0;hover=null;labelRoot?.replaceChildren();hashHits?.replaceChildren();if(hashHits)hashHits.hidden=true;if(!overlay())document.querySelector('#view-label').textContent='Perspective / drag to explore';wake();}
  else if(stored){syncLabels();paint();wake();}
  else getVoxels();
}
const VOXEL_PREF='gitirl-room-octree-v3';
function persistEnabled(on){try{localStorage.setItem(VOXEL_PREF,on?'on':'off');}catch{}}
document.querySelector('#enable-voxels').onchange=e=>{persistEnabled(e.target.checked);setEnabled(e.target.checked);};
toggleBtn?.addEventListener('click',()=>{
  const box=document.querySelector('#enable-voxels');
  box.checked=!box.checked;
  box.dispatchEvent(new Event('change'));
});
hashForm?.addEventListener('submit',e=>{e.preventDefault();playGen++;searchGeohash(hashQ?.value||'');});
hashClear?.addEventListener('click',()=>{playGen++;searchGeohash('');});
hashQ?.addEventListener('input',()=>{
  const key=parseGeohash(hashQ.value);
  if(key===null)return;
  hashQ.setCustomValidity('');
  clearTimeout(prefixTimer);
  prefixTimer=setTimeout(()=>{playGen++;searchGeohash(key);},280);
});
window.addEventListener('room:select-object',e=>{
  selection=e.detail.objectId;const commit=e.detail.commit;
  if(!enabled)setEnabled(true);
  if(stored&&(!commit||stored.commit_sha===commit)){if(!overlay())host.classList.add('voxel-active');rebuild();}
  else getVoxels(commit);
});
document.querySelector('#voxel-opacity').oninput=e=>{if(mesh){mesh.material.opacity=+e.target.value;wake();}};
document.querySelector('#voxel-level').onchange=()=>{
  level=document.querySelector('#voxel-level').value;
  if(stored?.source==='elasticsearch'||stored?.aggregated)getVoxels(stored.commit_sha);
  else if(stored){rebuild();fit(topView);}
};
document.querySelector('#voxel-prefix').oninput=e=>{
  if(!e.target.validity.valid)return;prefix=e.target.value;
  clearTimeout(prefixTimer);
  prefixTimer=setTimeout(()=>{
    if(stored?.source==='elasticsearch'||stored?.aggregated)getVoxels(stored.commit_sha);
    else if(stored){rebuild();fit(topView);}
  },180);
};
window.addEventListener('room:settings',()=>{
  if(renderer){renderer.setPixelRatio(Math.min(devicePixelRatio,+document.querySelector('#render-quality').value));renderer.setSize(host.clientWidth,host.clientHeight);controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;controls.zoomSpeed=+document.querySelector('#zoom-speed').value;}
  if(stored&&!load.disabled){
    const want=document.querySelector('#voxel-level').value;
    const sample=stored.cells[0]?.voxel_key||'';
    const have=stored.aggregated?levelFor(sample):level;
    if(!(stored.source==='elasticsearch'||stored.aggregated)||want===have)rebuild();
  }
  wake();
});
let voxelPref='off';try{voxelPref=localStorage.getItem(VOXEL_PREF)||'off';}catch{}
// Default to the leaf, not to L3. Measured on all history: the 1 m rung draws six cubes totalling
// 6 m^3 to show 483 L of occupancy -- 8% fill, four of them over 97% air. Fill by rung is
// L3 8.0%, L5 30.9%, L6 56.1%, leaf 100%.
document.querySelector('#voxel-level').value='full';
document.querySelector('#voxel-prefix').value='';
level='full';prefix='';
const params=new URLSearchParams(location.search);
const startKey=parseGeohash(params.get('prefix')||params.get('geohash')||'');
pinnedCommit=/^[0-9a-f]{40}$/.test(params.get('commit')||'')?params.get('commit'):null;
if(params.get('octree')==='1'||voxelPref==='on'||startKey||pinnedCommit)setEnabled(true);
if(startKey)searchGeohash(startKey);
window.roomVoxels={get state(){return{enabled,count:stored?.cells.length||0,displayed:displayed.length,level,prefix,commit:stored?.commit_sha,selection,focus:focus?.key,aggregated:!!stored?.aggregated,frameCount,drawCalls:renderer?.info.render.calls,camera:camera?.position.toArray(),up:camera?.up.toArray(),target:controls?.target?.toArray()};},play:playPrefix,search:searchGeohash};
