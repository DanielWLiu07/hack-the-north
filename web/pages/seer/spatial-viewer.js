// spatial-viewer.js — the orbitable 3-D panel under the Seer: point clouds, voxels, room map, objects.
//
// THIS IS THE PAGE'S SECOND WebGL CONTEXT, AND THAT IS A DECISION, NOT AN OVERSIGHT.
// The Seer (seer.js) holds the first. A reviewer counting contexts will want to merge them; please
// read this before you do, because both real ways of merging cost more than the thing they fix.
//
// A WebGLRenderer owns exactly one canvas, and these two draw in two different places on the page:
// the Seer's band at the top, and this panel further down. Sharing therefore means one of:
//   * one canvas spanning both regions, drawn with setScissor/setViewport per region — that is a
//     page-layout restructure, not a change to this file;
//   * rendering to a target and blitting into a 2-D canvas here — a full-frame copy, every frame,
//     for as long as the panel is open.
// A third idea, disposing this renderer whenever the panel is unused, trades the context for a
// shader recompile on every return: a stutter the visitor sees, in exchange for memory nobody is
// short of. Measured with two contexts: 60 fps, 32 ms worst frame, so the cost is memory and not
// smoothness. Decision 2026-09-19, with the front-end owner: keep two, stop here.
//
// What IS paid for and must stay:
//   * the context is created LAZILY — spatial-telemetry.js only imports this module when a
//     non-camera view is first opened, so a visitor who never opens one never makes a second one;
//   * release() disposes each object's OWN resources as well as its geometry and material. An
//     InstancedMesh keeps instanceMatrix and instanceColor outside its geometry, and only its own
//     dispose() frees them; without that, every voxel and room-map view leaked two GL buffers.
// Verify both with: node web/landing/tools/dev/framewatch.mjs http://127.0.0.1:8000 /telemetry <out> --exercise
// Three rounds of clicking every view must leave buffers, textures and programs flat.

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

export function createSpatialViewer(host) {
  const renderer = new THREE.WebGLRenderer({antialias:true, powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor('#080b12');
  const canvas=renderer.domElement;canvas.id='spatial-viewer';canvas.tabIndex=0;canvas.setAttribute('aria-label','Spatial view. Drag or arrow keys to orbit, wheel or plus/minus to zoom.');host.append(canvas);
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(48,1,.001,1000);
  const target=new THREE.Vector3();let root=null,grid=null,distance=4,yaw=.6,pitch=.45,raf=0,active=true,dead=false;
  function draw(){raf=0;if(dead||!active||document.hidden)return;
    camera.position.set(target.x+distance*Math.cos(pitch)*Math.sin(yaw),target.y+distance*Math.sin(pitch),target.z+distance*Math.cos(pitch)*Math.cos(yaw));
    camera.lookAt(target);renderer.render(scene,camera);
  }
  function wake(){if(!raf&&!dead&&active&&!document.hidden)raf=requestAnimationFrame(draw);}
  // size it from the host at once, not only when the observer first fires: a viewer built while its
  // panel is still hidden otherwise sits at the 300x150 default until something resizes it
  function size(){const w=host.clientWidth,h=host.clientHeight;if(!w||!h)return false;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();return true;}
  size();
  const resize=new ResizeObserver(()=>{if(size())wake();});resize.observe(host);
  // o.dispose() as well as geometry and material — see the header: an InstancedMesh's instance
  // buffers are freed by nothing else. Object3D has no dispose, so this is a no-op for plain
  // meshes, and it is idempotent for the helpers that do define one.
  function release(object){object?.traverse(o=>{o.geometry?.dispose();for(const m of Array.isArray(o.material)?o.material:[o.material])m?.dispose();o.dispose?.();});}
  function clear(){if(root){scene.remove(root);release(root);}if(grid){scene.remove(grid);release(grid);}root=grid=null;renderer.clear();wake();}
  function fit(top=false){if(!root)return;const b=new THREE.Box3().setFromObject(root);if(b.isEmpty())return;
    b.getCenter(target);const radius=Math.max(b.getSize(new THREE.Vector3()).length()/2,.08);
    distance=radius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))/Math.min(camera.aspect||1,1)*1.15;
    yaw=.6;pitch=top?Math.PI/2-.001:.45;camera.far=Math.max(100,distance*20);camera.updateProjectionMatrix();wake();
  }
  function mount(group){clear();root=group;scene.add(root);const b=new THREE.Box3().setFromObject(root);if(b.isEmpty())throw Error('No spatial geometry returned.');
    const size=b.getSize(new THREE.Vector3()),center=b.getCenter(new THREE.Vector3());
    grid=new THREE.GridHelper(Math.max(size.x,size.z,.25)*1.3,16,0x354454,0x192430);grid.position.set(center.x,b.min.y-.015,center.z);scene.add(grid);fit();
  }
  let drag=null;
  canvas.addEventListener('pointerdown',e=>{drag=[e.clientX,e.clientY];canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw-=(e.clientX-drag[0])*.006;pitch=THREE.MathUtils.clamp(pitch+(e.clientY-drag[1])*.006,-1.5,1.569);drag=[e.clientX,e.clientY];wake();});
  for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,()=>drag=null);
  canvas.addEventListener('wheel',e=>{e.preventDefault();distance=THREE.MathUtils.clamp(distance*Math.exp(e.deltaY*.001),.02,1000);wake();},{passive:false});
  canvas.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-'].includes(e.key))return;e.preventDefault();
    if(e.key==='ArrowLeft')yaw-=.15;if(e.key==='ArrowRight')yaw+=.15;if(e.key==='ArrowUp')pitch=Math.min(1.569,pitch+.12);if(e.key==='ArrowDown')pitch=Math.max(-1.5,pitch-.12);if(e.key==='+'||e.key==='=')distance=Math.max(.02,distance*.85);if(e.key==='-')distance=Math.min(1000,distance/ .85);wake();});
  const visibility=()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else wake();};document.addEventListener('visibilitychange',visibility);
  return {
    clear,
    async cloud(buffer,valid=()=>true){const gltf=await new GLTFLoader().parseAsync(buffer,'');if(!valid()){release(gltf.scene);return null;}
      let count=0;gltf.scene.traverse(o=>{if(o.isPoints){count+=o.geometry.attributes.position?.count||0;o.material.size=.012;o.material.sizeAttenuation=true;}});
      if(!count||count>250000){release(gltf.scene);throw Error('Point cloud is empty or exceeds the 250,000-point viewer limit.');}
      mount(gltf.scene);return count;
    },
    points(cells){
      const positions=[],colors=[],color=new THREE.Color();
      for(const c of cells){if(!c.center?.every(Number.isFinite))continue;positions.push(c.center[0],c.center[2],-c.center[1]);color.set(c.color||'#536c82');colors.push(color.r,color.g,color.b);}
      const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
      mount(new THREE.Points(geometry,new THREE.PointsMaterial({size:.025,vertexColors:true})));return positions.length/3;
    },
    voxels(cells){const valid=cells.filter(c=>c.center?.length===3&&c.center.every(Number.isFinite)&&Number.isFinite(c.size)&&c.size>0).slice(0,20000);
      if(!valid.length)throw Error('No valid stored voxels available.');
      const mesh=new THREE.InstancedMesh(new THREE.BoxGeometry(1,1,1),new THREE.MeshBasicMaterial({transparent:true,opacity:.72}),valid.length),matrix=new THREE.Matrix4(),color=new THREE.Color();
      valid.forEach((c,i)=>{matrix.makeScale(c.size*.91,c.size*.91,c.size*.91);matrix.setPosition(c.center[0],c.center[2],-c.center[1]);mesh.setMatrixAt(i,matrix);mesh.setColorAt(i,color.set(/^#[0-9a-f]{6}$/i.test(c.color)?c.color:c.object_id?'#ae9bff':'#536c82'));});mesh.computeBoundingSphere();mount(mesh);return valid.length;
    },
    objects(data){const group=new THREE.Group();let count=0;
      for(const o of (data.objects||[]).slice(0,1000)){const p=o.pose,e=o.extents;if(!p||!e||![p.x,p.y,p.z,e.x,e.y,e.z].every(Number.isFinite)||Math.min(e.x,e.y,e.z)<=0)continue;
        const mesh=new THREE.Mesh(new THREE.BoxGeometry(e.x,e.z,e.y),new THREE.MeshBasicMaterial({color:/^#[0-9a-f]{6}$/i.test(o.color)?o.color:'#a99de7',transparent:true,opacity:.85}));
        mesh.position.set(p.x,p.z,-p.y);mesh.rotation.y=(Number.isFinite(p.yaw)?p.yaw:0)*Math.PI/180;group.add(mesh);count++;
      }
      for(const zone of Object.values(data.zones||{})){if(!zone.min?.every(Number.isFinite)||!zone.max?.every(Number.isFinite)||zone.min.length!==3||zone.max.length!==3)continue;
        const b=new THREE.Box3(new THREE.Vector3(zone.min[0],zone.min[2],-zone.max[1]),new THREE.Vector3(zone.max[0],zone.max[2],-zone.min[1]));group.add(new THREE.Box3Helper(b,0x6d7786));}
      if(!count){release(group);throw Error('No recorded object positions available.');}mount(group);fit(true);return count;
    },
    fit,
    setActive(on){active=on;if(!on){cancelAnimationFrame(raf);raf=0;}else wake();},
    dispose(){dead=true;cancelAnimationFrame(raf);resize.disconnect();document.removeEventListener('visibilitychange',visibility);clear();renderer.dispose();canvas.remove();},
  };
}
