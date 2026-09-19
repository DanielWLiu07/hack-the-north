import * as THREE from 'three';
// A free-tumbling camera: no polar-angle clamp, ground lock, or bounded pan.
export class FreeCameraControls extends THREE.EventDispatcher {
  constructor(camera,canvas){super();this.camera=camera;this.canvas=canvas;this.target=new THREE.Vector3();this.offset=new THREE.Vector3(1,1,1);this.up=new THREE.Vector3(0,1,0);this.enableDamping=true;this.dampingFactor=.12;this.zoomSpeed=1;this.pointers=new Map();this.synced=false;
    canvas.style.touchAction='none';canvas.addEventListener('contextmenu',e=>e.preventDefault());
    canvas.addEventListener('pointerdown',e=>{canvas.focus({preventScroll:true});this.pointers.set(e.pointerId,{x:e.clientX,y:e.clientY,button:e.button});canvas.setPointerCapture(e.pointerId);});
    canvas.addEventListener('pointermove',e=>{
      const old=this.pointers.get(e.pointerId);if(!old)return;const dx=e.clientX-old.x,dy=e.clientY-old.y;
      if(this.pointers.size===2){const other=[...this.pointers.entries()].find(([id])=>id!==e.pointerId)[1];const before=Math.hypot(old.x-other.x,old.y-other.y),after=Math.hypot(e.clientX-other.x,e.clientY-other.y);this.pan(dx*.5,dy*.5);if(before>1&&after>1)this.dolly(before/after);}
      else if(old.button===2||e.shiftKey||e.ctrlKey||e.metaKey)this.pan(dx,dy);
      else if(old.button===1)this.dolly(Math.exp(dy*.008));
      else this.rotate(dx,dy);
      this.pointers.set(e.pointerId,{...old,x:e.clientX,y:e.clientY});this.dispatchEvent({type:'change'});
    });
    for(const name of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(name,e=>this.pointers.delete(e.pointerId));
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.dolly(Math.exp(THREE.MathUtils.clamp(e.deltaY*(e.deltaMode===1?16:1)*.001*this.zoomSpeed,-2,2)));this.dispatchEvent({type:'change'});},{passive:false});
    canvas.addEventListener('keydown',e=>{const k=e.key.toLowerCase();if(!['w','a','s','d','q','e','arrowleft','arrowright','arrowup','arrowdown','+','=','-'].includes(k))return;e.preventDefault();if(k==='a')this.pan(30,0);if(k==='d')this.pan(-30,0);if(k==='w')this.pan(0,30);if(k==='s')this.pan(0,-30);if(k==='arrowleft')this.rotate(-30,0);if(k==='arrowright')this.rotate(30,0);if(k==='arrowup')this.rotate(0,-30);if(k==='arrowdown')this.rotate(0,30);if(k==='q'||k==='e'){this.up.applyAxisAngle(this.offset.clone().normalize(),k==='q'?.12:-.12);}if(k==='+'||k==='=')this.dolly(.85);if(k==='-')this.dolly(1.18);this.dispatchEvent({type:'change'});});
  }
  sync(){this.offset.copy(this.camera.position).sub(this.target);this.up.copy(this.camera.up).normalize();this.synced=true;}
  rotate(dx,dy){if(!this.synced)this.sync();const forward=this.offset.clone().normalize(),right=new THREE.Vector3().crossVectors(this.up,forward).normalize();const yaw=new THREE.Quaternion().setFromAxisAngle(this.up,-dx*.006),pitch=new THREE.Quaternion().setFromAxisAngle(right,-dy*.006);this.offset.applyQuaternion(yaw).applyQuaternion(pitch);this.up.applyQuaternion(pitch).normalize();}
  pan(dx,dy){if(!this.synced)this.sync();const right=new THREE.Vector3().crossVectors(this.up,this.offset).normalize(),scale=2*this.offset.length()*Math.tan(THREE.MathUtils.degToRad(this.camera.fov/2))/Math.max(this.canvas.clientHeight,1);this.target.addScaledVector(right,-dx*scale).addScaledVector(this.up,dy*scale);}
  dolly(factor){if(!this.synced)this.sync();const length=this.offset.length();this.offset.multiplyScalar(THREE.MathUtils.clamp(length*factor,1e-5,1e9)/Math.max(length,1e-10));}
  update(){if(!this.synced)this.sync();const c=this.camera,desired=this.target.clone().add(this.offset),m=new THREE.Matrix4().lookAt(desired,this.target,this.up),q=new THREE.Quaternion().setFromRotationMatrix(m);const alpha=this.enableDamping?.22:1;c.position.lerp(desired,alpha);c.quaternion.slerp(q,alpha);c.up.copy(this.up);const distance=this.offset.length();c.near=Math.max(distance/10000,1e-7);c.far=Math.max(distance*100,1000);c.updateProjectionMatrix();return c.position.distanceToSquared(desired)>Math.max(distance*distance*1e-12,1e-16)||c.quaternion.angleTo(q)>1e-5;}
}
