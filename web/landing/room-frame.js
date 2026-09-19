// Screen-space 3D factory structure, behind the workspace. No layout footprint or input.
import * as THREE from 'three';
import { Rigid } from './mech.js';
const canvas=document.createElement('canvas');
canvas.className='room-frame';canvas.setAttribute('aria-hidden','true');document.body.prepend(canvas);
const renderer=new THREE.WebGLRenderer({canvas,alpha:true,antialias:true,powerPreference:'low-power'});
renderer.setPixelRatio(Math.min(devicePixelRatio,1.25));renderer.setClearColor(0,0);
const scene=new THREE.Scene(),camera=new THREE.OrthographicCamera(0,innerWidth,0,innerHeight,.1,2000);
camera.position.z=900;
scene.add(new THREE.AmbientLight('#d8d5cd',1.2));
const key=new THREE.DirectionalLight('#eeeae1',2.5);key.position.set(-300,-500,650);scene.add(key);
const fill=new THREE.DirectionalLight('#777777',.6);fill.position.set(500,300,200);scene.add(fill);
const metal=new THREE.MeshStandardMaterial({color:'#56534d',roughness:.86,metalness:.35});
const dark=new THREE.MeshStandardMaterial({color:'#20201d',roughness:.94,metalness:.12});
const edge=new THREE.MeshStandardMaterial({color:'#848074',roughness:.73,metalness:.4});
for(const mat of [metal,dark,edge]){
 mat.onBeforeCompile=shader=>{shader.fragmentShader=shader.fragmentShader.replace('#include <dithering_fragment>',`#include <dithering_fragment>
 float hatch=step(.84,fract((gl_FragCoord.x+gl_FragCoord.y)*.2));
 float grit=fract(sin(dot(floor(gl_FragCoord.xy),vec2(12.9898,78.233)))*43758.5453);
 gl_FragColor.rgb*=1.0-hatch*.17-grit*.07;`);};
 mat.customProgramCacheKey=()=> 'room-frame-ink';
}
let assembly,queued=0,frames=0;
const kit=()=>new Rigid();
const bolt=(builder,x,y,z=10)=>builder.add(new THREE.CylinderGeometry(3.4,3.4,3,6).rotateX(Math.PI/2),edge,x,y,z);
function bar(builder,x1,y1,x2,y2,width=16){
 const dx=x2-x1,dy=y2-y1,len=Math.hypot(dx,dy),angle=-Math.atan2(dx,dy),cx=(x1+x2)/2,cy=(y1+y2)/2;
 builder.add(new THREE.BoxGeometry(width,len,9).rotateY(.25).rotateZ(angle),dark,cx,cy,0);
 builder.add(new THREE.BoxGeometry(width*.68,len,4).rotateY(.25).rotateZ(angle),metal,cx,cy,6);
 for(let at=16;at<len;at+=76){const u=at/len;bolt(builder,x1+dx*u,y1+dy*u,10);}
}
function gear(parent,x,y,r,count,tilt=0){
 const g=new THREE.Group();g.position.set(x,y,0);g.rotation.set(.18,tilt,.08);parent.add(g);
 const b=kit();
 b.add(new THREE.TorusGeometry(r*.70,r*.14,6,48),metal);
 b.add(new THREE.CylinderGeometry(r*.23,r*.23,15,20).rotateX(Math.PI/2),dark);
 b.add(new THREE.CylinderGeometry(r*.11,r*.11,19,6).rotateX(Math.PI/2),edge);
 for(let i=0;i<count;i++){const a=i/count*Math.PI*2;
   b.add(new THREE.BoxGeometry(r*.19,r*.25,14).rotateZ(-a),metal,Math.sin(a)*r*.88,Math.cos(a)*r*.88,0);
 }
 for(let i=0;i<6;i++){const a=i/6*Math.PI*2;
   b.add(new THREE.BoxGeometry(r*.10,r*.47,9).rotateZ(-a),dark,Math.sin(a)*r*.43,Math.cos(a)*r*.43,-2);
   bolt(b,Math.sin(a)*r*.70,Math.cos(a)*r*.70,12);
 }
 b.into(g);
}
function pipe(builder,points,r=5){
 const curve=new THREE.CatmullRomCurve3(points.map(p=>new THREE.Vector3(p[0],p[1],4)),false,'centripetal');
 builder.add(new THREE.TubeGeometry(curve,36,r,8,false),metal);
 for(const p of [points[0],points[points.length-1]]){
   builder.add(new THREE.BoxGeometry(r*3.6,12,r*2.8),dark,p[0],p[1],4);bolt(builder,p[0],p[1],13);
 }
}
function draw(){
 queued=0;if(document.hidden)return;
 if(assembly){assembly.traverse(o=>{if(o.geometry)o.geometry.dispose();});scene.remove(assembly);}
 assembly=new THREE.Group();scene.add(assembly);
 const w=innerWidth,h=innerHeight,view=document.querySelector('.viewer').getBoundingClientRect();
 const panel=[document.querySelector('.agent-chat'),document.querySelector('#room-settings'),document.querySelector('.system-status')].find(e=>e&&!e.hidden&&(e.tagName!=='DETAILS'||e.open));
 const p=panel?.getBoundingClientRect();
 const b=kit(),top=view.top;
 // Real I-beams and bundled pipes remain around the edge of the data viewport.
 bar(b,-18,top+205,190,top-30,22);
 bar(b,8,h-7,w+20,h-7,21);
 bar(b,4,top-30,4,h+20,18);
 pipe(b,[[36,top-30],[36,top+125],[58,top+148],[220,top+148]],5);
 pipe(b,[[12,h-210],[64,h-210],[83,h-190],[83,h+20]],5);
 gear(assembly,18,top+10,86,18,-.25);
 gear(assembly,140,top-30,53,14,.1);
 if(p&&p.width){
   // Panel remains opaque and above these parts. Only mounting edges peek out.
   bar(b,p.left-9,p.top-36,p.left-9,h+28,13);
   bar(b,p.right+8,p.top-20,p.right+8,h+25,14);
   bar(b,p.left-26,p.top-8,p.right+26,p.top-8,13);
   pipe(b,[[p.left-23,p.top+120],[p.left-23,h-95],[p.left-3,h-75],[p.right+28,h-75]],4);
   gear(assembly,p.left-2,p.top+26,45,14,.35);
   gear(assembly,p.right-2,h-20,58,16,-.2);
   for(const y of [p.top+78,h-105]){
     b.add(new THREE.BoxGeometry(32,22,10),metal,p.left-2,y,9);bolt(b,p.left-13,y,16);
   }
 }else gear(assembly,w-6,h-12,110,20,.2);
 b.into(assembly);
 camera.right=w;camera.bottom=h;camera.updateProjectionMatrix();renderer.setSize(w,h);
 renderer.render(scene,camera);frames++;
}
function schedule(){if(!queued&&!document.hidden)queued=requestAnimationFrame(draw);}
const resize=new ResizeObserver(schedule);resize.observe(document.querySelector('.viewer'));
for(const el of document.querySelectorAll('.agent-chat,#room-settings,.system-status')){
 resize.observe(el);new MutationObserver(schedule).observe(el,{attributes:true,attributeFilter:['open','hidden']});
}
addEventListener('resize',schedule);document.addEventListener('visibilitychange',schedule);
window.roomFrame={renderer,get frames(){return frames;}};
schedule();
