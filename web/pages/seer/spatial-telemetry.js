import { SPATIAL_THEME } from './spatial-theme.js';
const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text!=null)node.textContent=String(text);if(cls)node.className=cls;return node;};
const show=v=>v==null?'not reported':String(v);
const MODES=[['camera','Live camera'],['cloud','Point cloud'],['voxels','Voxels'],['map','Room map'],['captures','Captured cameras'],['data','Objects & data']];
export function assetURL(path){
  if(typeof path!=='string'||!path.split('/').every(p=>/^[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9]+)?$/.test(p)))return null;
  return `/live/${path}`;
}
export function createSpatialTelemetry(host,cameraView,onCameraVisible){
  const root=el('section',null,'spatial-telemetry'),bar=el('div',null,'spatial-tabs');bar.setAttribute('role','tablist');bar.setAttribute('aria-label','Robot views');
  const panel=el('div',null,'spatial-panel');panel.setAttribute('role','tabpanel');panel.id='perception-panel';
  const status=el('p','Current head camera · select another view to inspect spatial evidence.','spatial-status');status.setAttribute('role','status');
  const toolbar=el('div',null,'spatial-toolbar'),refresh=el('button','Refresh data','navbtn'),reset=el('button','Fit view','navbtn'),top=el('button','Top view','navbtn');
  const cameraLabel=el('label','Capture camera '),select=el('select');select.setAttribute('aria-label','Capture camera');cameraLabel.append(select);
  for(const b of [refresh,reset,top])b.type='button';toolbar.append(cameraLabel,reset,top,refresh);toolbar.hidden=true;
  const stage=el('div',null,'spatial-stage'),content=el('div',null,'spatial-content');stage.hidden=true;content.hidden=true;
  panel.append(cameraView,toolbar,status,stage,content);root.append(bar,panel);host.prepend(root);
  let mode='camera',viewer=null,viewerTask=null,controller=null,generation=0,dead=false,inView=true,busy=false;
  let manifest=null,manifestKey='',currentCloud='',lastPaint='',pointCount=0,selectedCamera='';
  const cache=new Map(),buttons=new Map();
  const stylesheet=el('style',SPATIAL_THEME);root.append(stylesheet);
  function updateTabs(){for(const [key,b] of buttons){const active=key===mode;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;}panel.setAttribute('aria-labelledby',`perception-tab-${mode}`);}
  for(const [key,label] of MODES){const b=el('button',label);b.type='button';b.id=`perception-tab-${key}`;b.setAttribute('role','tab');b.setAttribute('aria-controls',panel.id);b.onclick=()=>switchMode(key);bar.append(b);buttons.set(key,b);}
  bar.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();let i=MODES.findIndex(([k])=>k===mode);i=e.key==='Home'?0:e.key==='End'?MODES.length-1:(i+(e.key==='ArrowRight'?1:-1)+MODES.length)%MODES.length;switchMode(MODES[i][0]);buttons.get(mode).focus();});
  updateTabs();
  const observer=new IntersectionObserver(entries=>{inView=entries[0].isIntersecting;viewer?.setActive(inView&&['cloud','voxels','map'].includes(mode));});observer.observe(root);
  async function get(url,signal,force=false){const old=cache.get(url);if(!force&&old&&Date.now()-old.at<15000)return old.data;
    const response=await fetch(url,{signal,cache:'no-store'});let data;try{data=await response.json();}catch{throw Error(`Data unavailable (HTTP ${response.status}).`);}
    if(!response.ok)throw Error(data.detail||data.message||`Data unavailable (HTTP ${response.status}).`);
    cache.set(url,{at:Date.now(),data});return data;
  }
  async function ensureViewer(){if(!viewerTask)viewerTask=import('./spatial-viewer.js').then(({createSpatialViewer})=>{if(dead)return null;viewer=createSpatialViewer(stage);return viewer;}).catch(e=>{viewerTask=null;throw e;});return viewerTask;}
  function raw(data){const d=el('details',null,'spatial-raw');d.append(el('summary','All source fields'),el('pre',JSON.stringify(data,null,2)));return d;}
  function facts(entries){const dl=el('dl',null,'spatial-facts');for(const [k,v]of entries)dl.append(el('dt',k),el('dd',show(v)));return dl;}
  function captureStamp(data){return `${data.sender_mode==='hardware'?'HARDWARE CAPTURE':data.sender_mode==='sim'?'SIMULATED CAPTURE':data.sender_mode==='recorded'?'RECORDED SESSION':data.sender_mode==='replay'?'REPLAY CAPTURE':'SOURCE UNVERIFIED'} · ${show(data.capture_id)} · captured ${show(data.at)} · received ${show(data.received_at)}`;}
  function describeCamera(cam){return facts([['Camera',cam.camera],['Model',cam.model],['Points',cam.points],['Intrinsics',cam.intrinsics?cam.intrinsics.assumed_from_hfov_deg?'assumed; approximate geometry':'provided by source':'not reported'],['Cloud',cam.cloud?'available':cam.cloud_reason||'not provided']]);}
  async function loadManifest(signal,force){const data=await get('/live/latest.json',signal,force);if(!Array.isArray(data.cameras))throw Error('No camera manifest is available.');manifest=data;
    const key=JSON.stringify(data.cameras.map(c=>[c.camera,c.cloud]));
    if(key!==manifestKey){manifestKey=key;const previous=selectedCamera;select.replaceChildren();for(const c of data.cameras){const option=el('option',`${c.camera} · ${c.cloud?'cloud':'colour only'}`);option.value=c.camera;select.append(option);}select.value=data.cameras.some(c=>c.camera===previous)?previous:data.cameras.find(c=>c.cloud)?.camera||data.cameras[0]?.camera||'';selectedCamera=select.value;}
    return data;
  }
  function images(data){const grid=el('div',null,'spatial-images');for(const cam of data.cameras){const card=el('article');card.append(el('h3',`${cam.camera} · ${cam.model||'camera model not reported'}`));const url=assetURL(cam.color);
      if(url){const img=el('img');img.alt=`${cam.camera}: recorded colour frame from ${data.capture_id}`;img.loading='lazy';img.src=url;img.onerror=()=>{img.replaceWith(el('p','Recorded colour frame unavailable.','spatial-warning'));};card.append(img);}else card.append(el('p','No colour frame provided.'));
      card.append(describeCamera(cam));if(cam.cloud){const b=el('button','Inspect point cloud','navbtn');b.type='button';b.onclick=()=>{selectedCamera=cam.camera;select.value=cam.camera;switchMode('cloud');};card.append(b);}grid.append(card);}return grid;
  }
  function objects(data){const wrap=el('div',null,'spatial-table-wrap'),table=el('table'),head=el('thead'),tr=el('tr');for(const h of ['Object','Zone','Position · metres','Yaw','Size · metres'])tr.append(el('th',h));head.append(tr);table.append(head);const body=el('tbody');
    for(const o of (data.objects||[]).slice(0,1000)){const row=el('tr'),name=el('td'),a=el('a',o.class||o.object_id);a.href=`/object/${encodeURIComponent(o.object_id)}`;name.append(a,el('small',o.object_id));row.append(name,el('td',show(o.zone)),el('td',['x','y','z'].map(k=>show(o.pose?.[k])).join(', ')),el('td',`${show(o.pose?.yaw)}°`),el('td',['x','y','z'].map(k=>show(o.extents?.[k])).join(' × ')));body.append(row);}table.append(body);wrap.append(table);return wrap;
  }
  async function load(force=false){if(dead||mode==='camera')return;controller?.abort();controller=new AbortController();const ctl=controller,id=++generation,chosenMode=mode;const valid=()=>!dead&&id===generation&&mode===chosenMode;const timer=setTimeout(()=>ctl.abort(),15000);busy=true;refresh.disabled=true;
    status.textContent='Loading recorded spatial evidence…';status.classList.remove('spatial-warning');
    try{
      if(mode==='cloud'||mode==='captures'){
        const data=await loadManifest(ctl.signal,force);if(!valid())return;status.textContent=captureStamp(data);
        if(mode==='captures'){content.replaceChildren(images(data),raw(data));return;}
        const cam=data.cameras.find(c=>c.camera===selectedCamera);if(!cam)throw Error('No camera has reported a capture.');
        content.replaceChildren(el('p',`${data.axes||'Camera coordinate frame'} · ${data.units||'units not reported'}. Each camera is shown separately; registration to the room has not been applied.`),describeCamera(cam),facts([['Quality gate',data.quality_ok==null?'not reported':data.quality_ok?'passed at capture':'rejected'],['Camera skew',data.skew_ms==null?null:`${data.skew_ms} ms`],['Peak tilt',data.tilt_rate_max==null?null:`${data.tilt_rate_max} rad/s`],['Depth coverage',data.coverage],['Pose source',data.pose_source]]),raw(data));
        const url=assetURL(cam.cloud);if(!url){viewer?.clear();currentCloud='';throw Error(cam.cloud_reason||'This camera has no point cloud.');}
        const key=`${data.capture_id}:${data.received_at}:${cam.camera}:${url}`;
        const v=await ensureViewer();if(!valid()||!v)return;
        if(currentCloud!==key||lastPaint!=='cloud'){
          const response=await fetch(url,{signal:ctl.signal,cache:'no-store'});if(!response.ok)throw Error(`Point cloud unavailable (HTTP ${response.status}).`);
          const buffer=await response.arrayBuffer();if(!valid())return;if(buffer.byteLength>20000000)throw Error('Point cloud exceeds the 20 MB preview limit.');
          pointCount=await v.cloud(buffer,valid);if(!valid())return;currentCloud=key;lastPaint='cloud';
        }
        v.setActive(inView);status.textContent+=` · ${pointCount.toLocaleString()} points · drag to orbit`;
      }else if(mode==='voxels'){
        const data=await get('/api/voxels?limit=12000',ctl.signal,force);if(!valid())return;
        content.replaceChildren(el('p',data.provenance?.detail||'Sensor provenance has not been recorded.'),facts([['Commit',data.commit_sha],['Snapshot recorded',data.snapshot?.timestamp],['Snapshot selection',data.snapshot_source],['Source',data.source],['Total cells',data.total],['Returned',data.returned],['Invalid cells',data.invalid],['Units',data.units],['Frame',data.frame]]),raw(data));
        const v=await ensureViewer();if(!valid()||!v)return;const count=v.voxels(data.cells||[]);lastPaint='voxels';currentCloud='';v.setActive(inView);
        status.textContent=`STORED VOXELS · ${count.toLocaleString()} cells${data.truncated?' · PARTIAL SNAPSHOT':''} · recorded ${show(data.snapshot?.timestamp)} · provenance ${data.provenance?.kind||'unknown'}`;
      }else{
        const [room,nav,ci]=await Promise.allSettled([get('/api/state',ctl.signal,force),get('/api/nav/snapshot',ctl.signal,force),get('/api/room/ci',ctl.signal,force)]);if(!valid())return;
        if(room.status==='rejected')throw room.reason;const data=room.value;
        const navData=nav.status==='fulfilled'?nav.value:{unavailable:nav.reason.message},ciData=ci.status==='fulfilled'?ci.value:{unavailable:ci.reason.message};
        const note=el('p',nav.status==='rejected'?`Live navigation unavailable: ${nav.reason.message}`:'Navigation response available in source fields; no pose is overlaid without verified frame registration.','spatial-warning');
        content.replaceChildren(note,facts([['Recorded commit',data.sha],['Frame',data.frame],['Objects',(data.objects||[]).length],['Room state',ciData.state],['Last capture',ciData.last_capture],['Heartbeat',ciData.heartbeat?.last]]),objects(data),raw({room:data,navigation:navData,room_health:ciData}));
        status.textContent=`RECORDED ROOM STATE · ${(data.objects||[]).length} objects · ${String(data.sha||'unknown').slice(0,8)} · not a live camera reconstruction`;
        if(mode==='map'){const v=await ensureViewer();if(!valid()||!v)return;v.objects(data);lastPaint='map';currentCloud='';v.setActive(inView);}
      }
    }catch(error){if(valid()){viewer?.clear();currentCloud='';status.textContent=error.name==='AbortError'?'Data request timed out. Refresh to retry.':error.message;status.classList.add('spatial-warning');}}
    finally{clearTimeout(timer);if(valid()){busy=false;refresh.disabled=false;}}
  }
  function switchMode(next){if(dead)return;mode=next;generation++;controller?.abort();busy=false;refresh.disabled=false;updateTabs();const cameraOn=mode==='camera',spatial=['cloud','voxels','map'].includes(mode);
    cameraView.hidden=!cameraOn;toolbar.hidden=cameraOn;stage.hidden=!spatial;content.hidden=cameraOn;cameraLabel.hidden=mode!=='cloud';reset.hidden=top.hidden=!spatial;content.replaceChildren();viewer?.clear();currentCloud='';viewer?.setActive(spatial&&inView);onCameraVisible(cameraOn);
    status.textContent=cameraOn?'Current head camera · lens controls change only the camera view.':'';load();
  }
  refresh.onclick=()=>load(true);reset.onclick=()=>viewer?.fit();top.onclick=()=>viewer?.fit(true);select.onchange=()=>{selectedCamera=select.value;load();};
  const tick=setInterval(()=>{if(!dead&&!busy&&!document.hidden&&inView&&mode!=='camera')load(true);},30000);
  return {get cameraVisible(){return mode==='camera';},dispose(){dead=true;generation++;controller?.abort();clearInterval(tick);observer.disconnect();viewer?.dispose();root.remove();}};
}
