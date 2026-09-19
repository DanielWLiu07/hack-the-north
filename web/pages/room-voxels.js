import * as THREE from 'three';
import { FreeCameraControls as OrbitControls } from './room-camera.js';

const status=document.querySelector('#voxel-status'), load=document.querySelector('#load-voxels');
const clear=document.querySelector('#clear-voxel-selection'), host=document.querySelector('.viewer');
let renderer,scene,camera,controls,mesh,grid,box,raf=0,controller,requestId=0,stored=null,selection=null,frameCount=0,topView=false;
let displayed=[],prefix='',level='full';
let enabled=false;
const white=new THREE.Color('#efede8'),dim=new THREE.Color('#292d33'),base=new THREE.Color('#9199a2');
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
function render(){raf=0;if(!enabled||document.hidden||!renderer)return;const moving=controls.update();renderer.render(scene,camera);frameCount++;if(moving)wake();}
function wake(){if(enabled&&!raf&&!document.hidden)raf=requestAnimationFrame(render);}
function init(){
  if(renderer)return;
  renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'low-power'});renderer.setPixelRatio(Math.min(devicePixelRatio,+document.querySelector('#render-quality').value));renderer.setClearColor('#08090a');
  renderer.domElement.className='voxel-canvas';renderer.domElement.tabIndex=0;renderer.domElement.setAttribute('aria-label','Stored room voxels. Drag to orbit, scroll to zoom. Orbit and Top buttons provide alternate views.');host.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(45,1,.001,1000);controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;controls.zoomSpeed=+document.querySelector('#zoom-speed').value;controls.dampingFactor=.12;controls.addEventListener('change',wake);
  let viewportWidth=innerWidth,viewportHeight=innerHeight;
  const resize=()=>{renderer.setSize(host.clientWidth,host.clientHeight);camera.aspect=host.clientWidth/host.clientHeight;camera.updateProjectionMatrix();const viewportChanged=viewportWidth!==innerWidth||viewportHeight!==innerHeight;viewportWidth=innerWidth;viewportHeight=innerHeight;if(mesh&&viewportChanged)fit(topView);wake();};new ResizeObserver(resize).observe(host);resize();
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else wake();});
  reduced.addEventListener('change',()=>{controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;wake();});
  for(const id of ['perspective','reset','top'])document.getElementById(id).addEventListener('click',()=>{if(stored)fit(id==='top');});
  document.querySelector('#show-grid').addEventListener('change',()=>{if(grid)grid.visible=document.querySelector('#show-grid').checked;wake();});
  document.querySelector('#show-bounds').addEventListener('change',()=>{if(box)box.visible=document.querySelector('#show-bounds').checked;wake();});
}
function fit(top=false){
  topView=top;
  if(!mesh||!displayed.length)return;const bounds=mesh.geometry.boundingBox,center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const radius=Math.max(size.length()/2,.05),distance=radius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(camera.aspect,1)*1.1;
  controls.target.copy(center);camera.up.set(0,1,0);camera.position.copy(center).add(new THREE.Vector3(top?.001:1,top?1:.8,top?0:1).normalize().multiplyScalar(distance));camera.lookAt(center);controls.sync();controls.update();document.querySelector('#view-label').textContent=top?'Voxels / top / metres':'Drag: rotate · right-drag: pan · wheel: zoom';wake();
}
function paint(){
  let matched=0;const colors=mesh.geometry.attributes.color;
  displayed.forEach((c,i)=>{const yes=selection&&c.owners.has(selection);if(yes)matched++;const color=selection?(yes?white:dim):base;for(let j=0;j<24;j++)colors.setXYZ(i*24+j,color.r,color.g,color.b);});colors.needsUpdate=true;
  clear.hidden=!selection;
  const cached=stored.source==='cached_elasticsearch'?'Cached Elasticsearch snapshot':'Elasticsearch';
  status.textContent=`${cached} · ${displayed.length.toLocaleString()} ${level==='full'?'cells':'occupied prefix regions'}${prefix?` under ${prefix}`:''} · ${stored.commit_sha.slice(0,8)}. ${selection?`${matched} regions contain ${selection}. `:''}${stored.truncated?'Partial snapshot. ':''}Provenance: ${stored.provenance?.kind||'unknown'}.`;
  document.querySelector('#view-label').textContent='Voxels / metres / drag to orbit';wake();
}
function rebuild(){
  level=document.querySelector('#voxel-level').value;prefix=document.querySelector('#voxel-prefix').value;
  if(mesh){scene.remove(mesh);mesh.geometry.dispose();mesh.material.dispose();}for(const item of [grid,box])if(item){scene.remove(item);item.geometry.dispose();item.material.dispose();}
  const groups=new Map(),cube=stored.cube;
  const scope=document.querySelector('#voxel-scope').value;
  for(const c of stored.cells){if(prefix&&!c.voxel_key?.startsWith(prefix))continue;if(scope==='objects'&&!c.object_id||scope==='surfaces'&&c.object_id||scope==='selected'&&(!selection||c.object_id!==selection))continue;
    const depth=level==='full'?c.voxel_key?.length:Math.max(+level,prefix.length),key=c.voxel_key?.slice(0,depth)||String(groups.size);
    if(!groups.has(key)){let center=c.center,size=c.size;
      if(cube&&key.length<c.voxel_key.length){const lo=[...cube.origin];size=cube.size_m;for(const char of key){size/=2;[2,1,0].forEach((shift,axis)=>lo[axis]+=size*((+char>>shift)&1));}center=lo.map(v=>v+size/2);}
      groups.set(key,{center,size,key,owners:new Set()});}
    if(c.object_id)groups.get(key).owners.add(c.object_id);
  }
  displayed=[...groups.values()];
  const unit=new THREE.EdgesGeometry(new THREE.BoxGeometry(1,1,1)),edge=unit.attributes.position,positions=new Float32Array(displayed.length*72),colors=new Float32Array(displayed.length*72);
  displayed.forEach((c,i)=>{for(let j=0;j<24;j++){const k=i*72+j*3;positions[k]=c.center[0]+edge.getX(j)*c.size;positions[k+1]=c.center[2]+edge.getY(j)*c.size;positions[k+2]=-c.center[1]+edge.getZ(j)*c.size;}});unit.dispose();
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.BufferAttribute(colors,3));geometry.computeBoundingBox();
  mesh=new THREE.LineSegments(geometry,new THREE.LineBasicMaterial({vertexColors:true,transparent:true,opacity:+document.querySelector('#voxel-opacity').value,depthWrite:false,depthTest:true}));scene.add(mesh);
  grid=null;box=null;if(displayed.length){const b=geometry.boundingBox,size=b.getSize(new THREE.Vector3()),center=b.getCenter(new THREE.Vector3());grid=new THREE.GridHelper(Math.max(size.x,size.z,.1)*1.2,16,0x34383e,0x171a1f);grid.position.set(center.x,b.min.y,center.z);grid.visible=document.querySelector('#show-grid').checked;scene.add(grid);box=new THREE.Box3Helper(b,0x50555c);box.visible=document.querySelector('#show-bounds').checked;scene.add(box);}
  paint();wake();
}
function mount(data){
  if(!Array.isArray(data.cells)||!data.cells.length)throw Error('No stored voxels for this snapshot.');
  const cells=data.cells.filter(c=>Array.isArray(c.center)&&c.center.length===3&&c.center.every(Number.isFinite)&&Number.isFinite(c.size)&&c.size>0).slice(0,20000);
  if(!cells.length)throw Error('No valid voxel geometry returned.');
  init();stored={...data,cells};rebuild();host.classList.add('voxel-active');fit();
}
async function fetchJSON(url,signal){const r=await fetch(url,{signal});if(!r.ok){const error=new Error(`Voxel endpoint HTTP ${r.status}`);error.status=r.status;throw error;}return r.json();}
async function getVoxels(commit){
  controller?.abort();controller=new AbortController();const ctl=controller,id=++requestId,timer=setTimeout(()=>ctl.abort(),15000);load.disabled=true;status.textContent='Loading stored voxel geometry…';
  try{let data;try{data=await fetchJSON(`/api/voxels?${new URLSearchParams({limit:'20000',...(commit?{commit_sha:commit}:{})})}`,ctl.signal);}catch(e){if(e.status!==404)throw e;data=await fetchJSON('/models/room-voxels-snapshot.json',ctl.signal);if(commit&&data.commit_sha!==commit)throw Error('Requested snapshot is not in the cache. Live voxel API requires activation.');}
    if(id!==requestId||!enabled)return;mount(data);load.textContent='Refresh voxels';
  }catch(e){if(id===requestId)status.textContent=`${stored?'Previous snapshot remains visible. ':''}${e.name==='AbortError'?'Voxel request timed out.':e.message}`;}
  finally{clearTimeout(timer);if(id===requestId)load.disabled=false;}
}
load.onclick=()=>{if(!enabled)setEnabled(true);else getVoxels(stored?.commit_sha);};
clear.onclick=()=>{selection=null;if(stored)rebuild();};
function setEnabled(on){enabled=on;document.querySelector('#enable-voxels').checked=on;document.body.classList.toggle('voxels-enabled',on);host.classList.toggle('voxel-active',on&&!!stored);if(renderer)renderer.domElement.hidden=!on;if(!on){controller?.abort();requestId++;load.disabled=false;cancelAnimationFrame(raf);raf=0;document.querySelector('#view-label').textContent='Perspective / drag to explore';}else if(stored){paint();wake();}else getVoxels();}
const VOXEL_PREF='gitirl-room-voxels-v1';
document.querySelector('#enable-voxels').onchange=e=>{try{localStorage.setItem(VOXEL_PREF,e.target.checked?'on':'off');}catch{}setEnabled(e.target.checked);};
window.addEventListener('room:select-object',e=>{selection=e.detail.objectId;const commit=e.detail.commit;if(!enabled){enabled=true;document.querySelector('#enable-voxels').checked=true;document.body.classList.add('voxels-enabled');if(renderer)renderer.domElement.hidden=false;}if(stored&&(!commit||stored.commit_sha===commit)){host.classList.add('voxel-active');rebuild();}else getVoxels(commit);});
document.querySelector('#voxel-opacity').oninput=e=>{if(mesh){mesh.material.opacity=+e.target.value;wake();}};
document.querySelector('#voxel-level').onchange=e=>{level=e.target.value;if(stored){rebuild();fit(topView);}};
document.querySelector('#voxel-prefix').oninput=e=>{if(!e.target.validity.valid)return;prefix=e.target.value;if(stored){rebuild();fit(topView);}};
window.addEventListener('room:settings',()=>{if(renderer){renderer.setPixelRatio(Math.min(devicePixelRatio,+document.querySelector('#render-quality').value));renderer.setSize(host.clientWidth,host.clientHeight);controls.enableDamping=!reduced.matches&&document.querySelector('#camera-smoothing').checked;controls.zoomSpeed=+document.querySelector('#zoom-speed').value;}if(stored)rebuild();wake();});
// Voxels are the room: shown on arrival unless this browser switched them off.
let voxelPref='on';try{voxelPref=localStorage.getItem(VOXEL_PREF)||'on';}catch{}
if(voxelPref!=='off')setEnabled(true);
window.roomVoxels={get state(){return{enabled,count:stored?.cells.length||0,displayed:displayed.length,level,prefix,commit:stored?.commit_sha,selection,frameCount,drawCalls:renderer?.info.render.calls,camera:camera?.position.toArray(),up:camera?.up.toArray(),target:controls?.target.toArray()};}};
