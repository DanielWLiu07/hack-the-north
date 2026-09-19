import * as THREE from 'three';

// These edge limbs arrive after the character docks. No imported props.
export function createDecor(scene,enabled,skin,buildPalm) {
  if(!enabled)return {update(){},dispose(){},debug:()=>0};
  const root=new THREE.Group();scene.add(root);root.visible=false;
  const items=[],sides=10,materials=[];
  function shade(depth) {                  // same skin, pushed toward Seer's shadow violet: distance, not a new colour
    const m=skin.clone(),uDepth={value:depth};
    m.onBeforeCompile=shader=>{            // the skin shader paints its own inks, so the tint has to happen in there
      skin.onBeforeCompile(shader);shader.uniforms.uDepth=uDepth;
      shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nuniform float uDepth;')
        .replace('#include <opaque_fragment>','outgoingLight *= mix(vec3(1.0), vec3(0.50, 0.42, 0.80), uDepth);\n#include <opaque_fragment>');
    };
    m.customProgramCacheKey=()=>'seer-decor-depth';
    m.transparent=false;m.opacity=1;m.depthWrite=true;
    materials.push(m);return m;
  }
  let material=shade(0);const near=material;
  const point=new THREE.Vector3(),tangent=new THREE.Vector3(),normal=new THREE.Vector3();
  const axis=new THREE.Vector3(0,0,1),binormal=new THREE.Vector3();
  const ease=t=>{t=Math.max(0,Math.min(1,t));return t*t*(3-2*t);};
  function tube(rings=48) {
    const g=new THREE.BufferGeometry(),indices=[];
    g.setAttribute('position',new THREE.Float32BufferAttribute(new Float32Array((rings+1)*sides*3),3));
    g.setAttribute('normal',new THREE.Float32BufferAttribute(new Float32Array((rings+1)*sides*3),3));
    for(let r=0;r<rings;r++)for(let s=0;s<sides;s++){
      const a=r*sides+s,b=r*sides+(s+1)%sides;indices.push(a,b,a+sides,b,b+sides,a+sides);
    }
    g.setIndex(indices);const mesh=new THREE.Mesh(g,material);mesh.frustumCulled=false;mesh.userData.rings=rings;return mesh;
  }
  const finger=new THREE.CubicBezierCurve3(new THREE.Vector3(),new THREE.Vector3(),new THREE.Vector3(),new THREE.Vector3());
  function skinCurve(mesh,curve,radius,tipRadius) {
    const p=mesh.geometry.attributes.position,n=mesh.geometry.attributes.normal,rings=mesh.userData.rings;
    for(let r=0;r<=rings;r++){
      // forward difference into scratch vectors: Curve.getTangent allocates two Vector3 per ring
      const t=r/rings,t0=Math.min(t,1-1e-3);curve.getPoint(t0,point);curve.getPoint(t0+1e-3,tangent);
      tangent.sub(point).normalize();if(t0!==t)curve.getPoint(t,point);
      normal.crossVectors(tangent,axis).normalize();binormal.crossVectors(tangent,normal).normalize();
      const size=radius+(tipRadius-radius)*t;
      for(let s=0;s<sides;s++){
        const angle=s/sides*Math.PI*2,x=Math.cos(angle),y=Math.sin(angle),i=r*sides+s;
        const nx=normal.x*x+binormal.x*y,ny=normal.y*x+binormal.y*y,nz=normal.z*x+binormal.z*y;
        p.setXYZ(i,point.x+nx*size,point.y+ny*size,point.z+nz*size);n.setXYZ(i,nx,ny,nz);
      }
    }p.needsUpdate=n.needsUpdate=true;
  }
  // edge, offset along it, size. 'corner' limbs fill the top-left: [_, rootY, size, wristX, wristY, finger angle, z]
  // plus a depth tint, in desktop px. They leave the LEFT edge and rise in nested J-curves (lowest root reaches furthest,
  // so none cross), smaller, higher and deeper violet the further back they are, and sit behind Seer's own upper hand.
  const placements=[
    ['right',.19,.85],['right',.41,1.35],['right',.65,.78],['right',.89,1.08],
    ['top',.58,1.08],['top',.82,.82],['left',.69,.76],['left',.96,1.12],
    ['corner',310,.95,34,182,106,-300,.1],['corner',270,.8,146,140,64,-310,.35],
    ['corner',210,.62,112,108,82,-320,.6],['corner',260,.44,74,96,92,-330,.85],
  ];
  for(let i=0;i<placements.length;i++){
    material=placements[i][7]?shade(placements[i][7]):near;
    const group=new THREE.Group(),hand=new THREE.Group(),arm=tube(),palm=new THREE.Mesh(buildPalm(),material);
    const digits=Array.from({length:4},()=>tube(12));
    const tips=Array.from({length:4},()=>new THREE.Mesh(new THREE.SphereGeometry(4.2,8,6),material));
    const pts=Array.from({length:6},()=>new THREE.Vector3());
    hand.add(palm,...digits,...tips);group.add(arm,hand);root.add(group);
    palm.rotation.z=Math.PI;palm.scale.setScalar(34);
    items.push({group,arm,hand,digits,tips,pts,curve:new THREE.CatmullRomCurve3(pts,false,'centripetal')});
  }
  let arrived=null;
  return {
    update(w,h,focus,time,reduced,state){
      if(focus>0){root.visible=false;arrived=null;return;}
      if(arrived===null)arrived=time;
      root.visible=true;
      items.forEach(({group,arm,hand,digits,tips,pts,curve},i)=>{
        const [edge,offset,size]=placements[i],top=edge==='top',left=edge==='left',corner=edge==='corner';
        const mobile=w<760;group.visible=corner?w>=1180:!mobile||[0,2,4].includes(i);   // the corner fan needs the wide left gutter
        if(!group.visible)return;
        const reveal=reduced?1:ease((time-arrived-.12-i*.1)/1.1);
        const energy=reduced?0:state==='thinking'?1.35:state==='verdict'?0:.7;
        let angle,radius=11,curlAmp=7,phase;
        if(corner){
          // Barely alive: a slow breath that starts in the belly of the arm and arrives late at the wrist.
          const [,rootY,,wristX,wristY,deg,z]=placements[i];
          phase=reduced?0:time*.42+i*1.9;
          const wave=Math.sin(phase)*energy*.5,follow=Math.sin(phase-1.1)*energy*.5;
          const dx=wristX/size,dy=(rootY-wristY)/size;
          group.position.set(-(1-reveal)*130,-rootY-(1-reveal)*190,z);   // they rise out from behind Seer
          group.rotation.z=0;group.scale.setScalar(size);
          angle=deg*Math.PI/180+Math.PI+follow*.07;curlAmp=3;radius=12;
          pts[5].set(dx+follow*4,dy+wave*3,0);
          pts[4].set(pts[5].x+Math.cos(angle)*38,pts[5].y+Math.sin(angle)*38,0);
          pts[0].set(-90,8,0);pts[1].set(-12,0,0);
          pts[2].set(dx*.45,-dy*.08-8+wave*7,0);pts[3].set(pts[5].x+Math.cos(angle)*92+follow*3,pts[5].y+Math.sin(angle)*92,0);
        }else{
          phase=reduced?0:time*.65+i*2.1;
          const wave=Math.sin(phase)*energy,follow=Math.sin(phase-.85)*energy;
          // Every arm has its own scale and winding path; motion travels to the wrist.
          group.position.set(top?(mobile?w*.9:w*offset):left?-(1-reveal)*300:w+(1-reveal)*300,
            top?(1-reveal)*300-(mobile?35:10):-h*offset,25);
          group.rotation.z=top?Math.PI/2:left?Math.PI:0;group.scale.setScalar(size*(mobile?.52:1));
          angle=.35+follow*.32;if(i===0)radius=13;
          pts[5].set((top?-115:-38)-wave*11,(top?-18:0)+follow*17,0);
          pts[4].set(pts[5].x+Math.cos(angle)*38,pts[5].y+Math.sin(angle)*38,0);
          if(top){pts[0].set(170,70,0);pts[1].set(70,75+wave*18,0);pts[2].set(-20,60,0);pts[3].set(-55,-10-follow*15,0);}
          else{pts[0].set(135,-200,0);pts[1].set(-16,-160-wave*18,0);pts[2].set(-65-wave*12,-112,0);pts[3].set(-13,-56+follow*16,0);}
        }
        skinCurve(arm,curve,radius,8);
        hand.position.copy(pts[5]);hand.rotation.z=angle;
        digits.forEach((digit,j)=>{
          const y=j===3?-12:(j-1)*10,curl=Math.sin(phase-.5+j*.4)*curlAmp*energy,x=j===3?-23:-35;
          finger.v0.set(x,y,0);finger.v1.set(x-12,y+(j===3?-14:0),0);
          finger.v3.set(x-(j===3?14:34)+(j===1?-6:0),y+curl+(j===3?-19:(j-1)*7),0);
          finger.v2.set(finger.v3.x+8,finger.v3.y,0);
          skinCurve(digit,finger,5.2,4.2);
          tips[j].position.copy(finger.v3);
        });
      });
    },
    debug:()=>0,
    dispose(){root.traverse(o=>o.geometry?.dispose());scene.remove(root);materials.forEach(m=>m.dispose());},
  };
}
