import * as THREE from 'three';

// A short decorative overture. These marks never represent live telemetry.
const COLORS=['#f757b6','#ffb287','#6a5fc1','#fa7faa','#bba5ff','#f2d3ab'];
const smooth=(a,b,t)=>{const u=Math.max(0,Math.min(1,(t-a)/(b-a)));return u*u*(3-2*u);};
export function createIntroEffects(scene,enabled){
  if(!enabled)return {update(){},hide(){},dispose(){},debug:()=>0};
  const root=new THREE.Group();root.visible=false;scene.add(root);
  const geometries=[new THREE.TorusGeometry(1,.18,6,24),new THREE.TetrahedronGeometry(1),new THREE.OctahedronGeometry(1),new THREE.BoxGeometry(1.5,.3,.3)];
  const meshes=geometries.map(g=>{const m=new THREE.InstancedMesh(g,new THREE.MeshBasicMaterial({transparent:true,depthWrite:false}),6);m.frustumCulled=false;root.add(m);return m;});
  const color=new THREE.Color(),matrix=new THREE.Matrix4(),rotation=new THREE.Quaternion(),euler=new THREE.Euler(),position=new THREE.Vector3(),scale=new THREE.Vector3();
  for(let k=0;k<4;k++)for(let i=0;i<6;i++)meshes[k].setColorAt(i,color.set(COLORS[(i+k)%COLORS.length]));
  const arcs=Array.from({length:3},(_,i)=>{const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array(81*3),3));const line=new THREE.Line(g,new THREE.LineBasicMaterial({color:COLORS[i],transparent:true,opacity:0,depthWrite:false}));line.frustumCulled=false;root.add(line);return line;});
  const rayGeometry=new THREE.BufferGeometry();rayGeometry.setAttribute('position',new THREE.BufferAttribute(new Float32Array(32*6),3));
  const rays=new THREE.LineSegments(rayGeometry,new THREE.LineBasicMaterial({color:'#fa7faa',transparent:true,opacity:0,depthWrite:false}));rays.frustumCulled=false;root.add(rays);
  return {
    update(w,h,t,reduced,focus){
      root.visible=!reduced&&focus>0&&t<5.55;if(!root.visible)return;
      const mobile=w<760,entry=smooth(.15,1.1,t),exit=smooth(3.3,5.5,t),alpha=entry*(1-exit);
      const cx=w/2,cy=-h*(mobile?.39:.44),r=Math.min(w*.34,h*.34),ry=r*(mobile?.9:.58);
      const gather=1-smooth(.1,1.4,t),scatter=smooth(3.35,5.45,t);
      for(let k=0;k<4;k++){
        const mesh=meshes[k];mesh.material.opacity=alpha*(k===3?.7:.9);
        for(let i=0;i<6;i++){
          const n=k*6+i,angle=n/24*Math.PI*2+t*(k%2?-.18:.16)+k*.21;
          const orbit=1+gather*.65+scatter*.9+(n%3)*.12;
          const x=cx+Math.cos(angle)*r*orbit,y=cy+Math.sin(angle)*ry*orbit;
          position.set(x,y,-490+(n%3)*12);
          euler.set(t*.35+n,t*.5+k,angle+t*.2);rotation.setFromEuler(euler);
          const size=(mobile?7:12)+(n%4)*(mobile?2:3),pop=entry*(1-scatter*.7);
          scale.setScalar(size*pop);matrix.compose(position,rotation,scale);mesh.setMatrixAt(i,matrix);
        }mesh.instanceMatrix.needsUpdate=true;
      }
      for(let i=0;i<3;i++){
        const line=arcs[i],p=line.geometry.attributes.position,spin=t*(i%2?-.35:.28)+i*2.1;
        line.material.opacity=alpha*(i===1?.34:.2);
        for(let j=0;j<=80;j++){const a=spin+j/80*Math.PI*1.25,rr=(.77+i*.13)*(1+gather*.25+scatter*.65);p.setXYZ(j,cx+Math.cos(a)*r*rr,cy+Math.sin(a)*ry*rr,-540);}
        p.needsUpdate=true;
      }
      // One expanding arrival ripple, then three restrained accents on the catch beats.
      let pulse=Math.max(0,1-Math.abs(t-.95)/.6);
      for(let i=0;i<3;i++)pulse+=Math.max(0,1-Math.abs(t-(1.75+i*.6))/.22)*.35;
      rays.material.opacity=alpha*pulse*.32;
      const p=rayGeometry.attributes.position;
      for(let i=0;i<32;i++){const a=i/32*Math.PI*2,inner=r*(.88+t*.025),outer=inner+(8+(i%4)*5)*pulse;
        p.setXYZ(i*2,cx+Math.cos(a)*inner,cy+Math.sin(a)*inner*.75,-530);p.setXYZ(i*2+1,cx+Math.cos(a)*outer,cy+Math.sin(a)*outer*.75,-530);}
      p.needsUpdate=true;
    },
    hide(){root.visible=false;},
    debug:()=>root.visible?24:0,
    dispose(){scene.remove(root);root.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});},
  };
}
