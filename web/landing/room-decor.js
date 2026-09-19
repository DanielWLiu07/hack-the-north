// Decorative camera rig in its own gutter. Never part of the measured room scene.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const host = document.querySelector('.room-decoration');
const wide = matchMedia('(min-width: 1001px)');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let renderer, camera, scene, rig, raf = 0, last = 0, elapsed = 0, aim = 0, target = 0;
const joints = [], lengths = [1.7, 1.5, 1.1], angles = [-0.24, 0.55, -0.40];
let visible = false;
const body = new THREE.MeshStandardMaterial({color:'#a8a49a',roughness:.88,metalness:.25});
const dark = new THREE.MeshStandardMaterial({color:'#292b2b',roughness:.78,metalness:.35});
const pin = new THREE.MeshStandardMaterial({color:'#877756',roughness:.73,metalness:.45});
const steel = new THREE.MeshStandardMaterial({color:'#ccc9bc',roughness:.35,metalness:.65});
const cable = new THREE.MeshStandardMaterial({color:'#151717',roughness:1});
function mesh(parent, geo, material, x=0,y=0,z=0) {
  const m = new THREE.Mesh(geo,material);m.position.set(x,y,z);parent.add(m);return m;
}
function rod(parent,a,b,r,material) {
  const delta=b.clone().sub(a);
  const m=mesh(parent,new THREE.CylinderGeometry(r,r,delta.length(),10),material);
  m.position.copy(a).add(b).multiplyScalar(.5);m.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());return m;
}
function grain(material) {
  material.onBeforeCompile = shader => {
    shader.fragmentShader = shader.fragmentShader.replace('#include <dithering_fragment>', `
      #include <dithering_fragment>
      float shade=dot(gl_FragColor.rgb,vec3(.299,.587,.114));
      float hatch=step(.80,fract((gl_FragCoord.x+gl_FragCoord.y)*.19));
      float grain=fract(sin(dot(floor(gl_FragCoord.xy),vec2(12.9898,78.233)))*43758.5453);
      gl_FragColor.rgb *= 1.0 - hatch*(1.0-smoothstep(.08,.55,shade))*.20 - grain*.035;
    `);
  };
  material.customProgramCacheKey=()=> 'room-etched-metal-v1';
}
[body,dark,pin,steel,cable].forEach(grain);
const V=(x,y,z=0)=>new THREE.Vector3(x,y,z);
function segment(parent,length,index) {
  const joint = new THREE.Group();parent.add(joint);joints.push(joint);
  if(index)joint.position.y=-lengths[index-1];
  // Paired clevis plates, a dark casting, a front-facing axle and fasteners.
  mesh(joint,new THREE.BoxGeometry(.32,length*.72,.28),body,0,-length*.48);
  const axle=mesh(joint,new THREE.CylinderGeometry(.20,.20,.64,16),body,0,0,0);axle.rotation.x=Math.PI/2;
  for(const side of [-1,1]){
    mesh(joint,new THREE.BoxGeometry(.095,length*.9,.39),dark,side*.24,-length*.42);
    const bolt=mesh(joint,new THREE.CylinderGeometry(.11,.11,.07,6),pin,0,0,side*.35);bolt.rotation.x=Math.PI/2;
  }
  rod(joint,V(-.30,-.22,.12),V(-.30,-length*.63,.12),.075,dark);
  rod(joint,V(-.30,-length*.45,.12),V(-.30,-length*.91,.12),.036,steel);
  const curve=new THREE.CatmullRomCurve3([V(.28,-.1,-.04),V(.43,-length*.45,-.06),V(.29,-length*.89,-.04)]);
  mesh(joint,new THREE.TubeGeometry(curve,16,.036,6,false),cable);
  return joint;
}
async function init() {
  if(renderer || !wide.matches)return;
  renderer = new THREE.WebGLRenderer({alpha:true,antialias:true,powerPreference:'low-power'});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.setClearColor(0,0);
  renderer.domElement.setAttribute('aria-hidden','true');host.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.OrthographicCamera(-1.4,1.4,4.3,-4.3,.1,30);
  camera.position.set(0,0,12);camera.lookAt(0,0,0);
  scene.add(new THREE.AmbientLight('#ffffff',1.25));
  const key=new THREE.DirectionalLight('#fff8ed',3);key.position.set(-3,5,7);scene.add(key);
  const fill=new THREE.DirectionalLight('#b9c7d2',1.1);fill.position.set(4,0,4);scene.add(fill);
  rig=new THREE.Group();rig.position.set(.15,4.8,0);scene.add(rig);
  let parent=rig;lengths.forEach((length,index)=>parent=segment(parent,length,index));
  // Standard flange joins the generated head's mounting socket to the last link.
  mesh(parent,new THREE.CylinderGeometry(.22,.22,.20,16),dark,0,-lengths[2],0);
  try {
    const gltf=await new GLTFLoader().loadAsync('/models/room-decor/camera-textured.glb');
    const model=gltf.scene,box=new THREE.Box3().setFromObject(model),size=box.getSize(new THREE.Vector3());
    model.position.sub(box.getCenter(new THREE.Vector3()));
    const head=new THREE.Group();head.add(model);head.scale.setScalar(1.65/Math.max(size.x,size.y,size.z));
    head.position.set(0,-lengths[2]-.78,.06);head.rotation.y=.20;parent.add(head);
    model.traverse(o=>{if(o.isMesh){const mats=Array.isArray(o.material)?o.material:[o.material];mats.forEach(m=>{m.roughness=Math.max(.55,m.roughness);grain(m);});}});
    host.dataset.ready='true';
    window.roomDecoration={renderer,scene,joints,head,get frames(){return frames;}};
  } catch(e) { host.hidden=true;console.warn('[room] decorative camera unavailable:',e.message);return; }
  new ResizeObserver(()=>{
    if(!host.clientWidth || !host.clientHeight)return;
    renderer.setSize(host.clientWidth,host.clientHeight);
    const halfH=4.3;camera.left=-halfH*host.clientWidth/host.clientHeight;camera.right=-camera.left;
    camera.top=halfH;camera.bottom=-halfH;camera.updateProjectionMatrix();wake();
  }).observe(host);
  wake();
}
let frames=0;
function wake(){if(renderer&&host.dataset.ready&&!raf&&wide.matches&&visible&&!document.hidden){last=performance.now();raf=requestAnimationFrame(frame);}}
function frame(now){
  raf=0;if(!wide.matches||!visible||document.hidden)return;
  const dt=Math.min(.05,(now-last)/1000);last=now;elapsed+=dt;
  aim+=(target-aim)*(1-Math.exp(-5*dt));
  const u=THREE.MathUtils.clamp(elapsed/.9,0,1),e=u*u*u*(u*(u*6-15)+10);
  rig.position.y=4.8+(reduced.matches?0:(1-e)*1.2);
  joints.forEach((j,i)=>{j.rotation.z=angles[i]+(reduced.matches?0:aim*[.016,-.022,.01][i]);});
  renderer.render(scene,camera);frames++;
  if((elapsed<.9&&!reduced.matches)||Math.abs(aim-target)>.001)raf=requestAnimationFrame(frame);
}
new IntersectionObserver(([entry])=>{visible=entry.isIntersecting;wake();}).observe(host);
wide.addEventListener('change',()=>{init();wake();});
addEventListener('pointermove',e=>{if(!wide.matches||reduced.matches)return;target=THREE.MathUtils.clamp((e.clientX/innerWidth-.5)*2,-1,1);wake();},{passive:true});
document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;}else wake();});
init();
