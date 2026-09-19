import * as THREE from 'three';
const ease = (a,b,t) => { const u=Math.max(0,Math.min(1,(t-a)/(b-a)));return u*u*(3-2*u); };
function bounds(meta) {
  const points=meta.letters.flatMap((letter,i)=>letter.parts.flatMap(part=>part.outer.map(([x,y])=>[x+letter.pen+i*40,y])));
  return {x:Math.min(...points.map(p=>p[0])),y:Math.min(...points.map(p=>p[1])),
    right:Math.max(...points.map(p=>p[0])),top:Math.max(...points.map(p=>p[1]))};
}
function wordmark(meta) {
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),b=bounds(meta);
  svg.setAttribute('viewBox',`0 0 ${b.right-b.x} ${b.top-b.y}`);svg.setAttribute('aria-hidden','true');svg.classList.add('seer-wordmark');
  meta.letters.forEach((letter,i)=>{
    const path=document.createElementNS(ns,'path');
    const draw=points=>points.map(([x,y],n)=>`${n?'L':'M'}${x+letter.pen+i*40-b.x} ${b.top-y}`).join(' ')+'Z';
    path.setAttribute('d',letter.parts.map(p=>[draw(p.outer),...p.holes.map(draw)].join(' ')).join(' '));
    path.setAttribute('fill','currentColor');path.setAttribute('fill-rule','evenodd');svg.append(path);
  });return svg;
}
export function createLettering(scene,canvas) {
  if(!canvas.closest('.seerband'))return {update(){},dispose(){}};
  const title=document.querySelector('.seer-room > .bandtext h1')||canvas.parentElement.querySelector('h1'),previous=title?.innerHTML;
  const material=new THREE.MeshStandardMaterial({color:'#fff5ff',emissive:'#bd78eb',emissiveIntensity:.32,roughness:.35,metalness:.3,transparent:true});
  const glowCanvas=document.createElement('canvas');glowCanvas.width=glowCanvas.height=128;
  const ctx=glowCanvas.getContext('2d'),gradient=ctx.createRadialGradient(64,64,0,64,64,64);
  gradient.addColorStop(0,'rgba(216,156,255,.65)');gradient.addColorStop(.35,'rgba(161,89,240,.28)');gradient.addColorStop(1,'rgba(115,55,220,0)');
  ctx.fillStyle=gradient;ctx.fillRect(0,0,128,128);
  const glowTexture=new THREE.CanvasTexture(glowCanvas);
  const glowMaterial=new THREE.SpriteMaterial({map:glowTexture,transparent:true,depthWrite:false,blending:THREE.AdditiveBlending});
  const glow=new THREE.Sprite(glowMaterial);glow.visible=false;scene.add(glow);
  let word=null,dead=false,heightRatio=.3;
  Promise.all(['sentry','telemetry'].map(name=>fetch(`/pages/seer/lettering/${name}.json`).then(r=>{if(!r.ok)throw Error('Missing lettering');return r.json();})))
    .then(async([sentry,telemetry])=>{
      if(dead)return;
      if(title){title.replaceChildren(wordmark(telemetry));title.setAttribute('aria-label','Telemetry');}
      const b=bounds(sentry);heightRatio=(b.top-b.y)/(b.right-b.x);
      const geos=[];
      for(const [i,letter] of sentry.letters.entries()){
        await new Promise(requestAnimationFrame);
        if(dead){geos.forEach(g=>g.dispose());return;}
        const shapes=letter.parts.map(part=>{
          const s=new THREE.Shape(part.outer.map(([x,y])=>new THREE.Vector2(x,y)));
          part.holes.forEach(h=>s.holes.push(new THREE.Path(h.map(([x,y])=>new THREE.Vector2(x,y)))));return s;
        });
        geos.push(new THREE.ExtrudeGeometry(shapes,{depth:35,bevelEnabled:true,bevelThickness:4,bevelSize:6,bevelSegments:1,curveSegments:1})
          .translate(letter.pen+i*40-(b.x+b.right)/2,-(b.y+b.top)/2,0).scale(1/(b.right-b.x),1/(b.right-b.x),1/(b.right-b.x)));
      }
      word=new THREE.Group();geos.forEach(g=>word.add(new THREE.Mesh(g,material)));word.visible=false;scene.add(word);
    }).catch(()=>{});
  return {
    update(w,h,t,reduced){
      if(!word)return;
      const enter=ease(.05,.75,t),leave=ease(3.05,4.2,t);
      word.visible=!reduced&&t<4.2;
      const size=Math.min(w*(w<760?.72:.40),440);
      word.scale.setScalar(size*(.96+.04*enter));
      const centerY=Math.max(h*(w<760?.15:.105),size*heightRatio*.5+28);
      word.position.set(w/2,-centerY+(1-enter)*180+leave*140,100);
      word.rotation.set(.12*(1-leave),Math.sin(t*1.8)*.045,(1-enter)*-.06);material.opacity=enter*(1-leave);
      word.children.forEach((letter,i)=>{
        const age=Math.max(0,t-.08-i*.055),pop=1-Math.exp(-age*10)*Math.cos(age*13);
        letter.position.y=(pop-1)*.16;
        letter.rotation.x=(1-pop)*.65;
      });
      material.emissiveIntensity=.28+Math.exp(-Math.pow((t-.85)*3,2))*.65;
      glow.visible=word.visible;glow.position.copy(word.position);glow.position.z-=15;
      glow.scale.set(size*1.45,size*heightRatio*2.8,1);
      glowMaterial.opacity=enter*(1-leave)*(.6+Math.exp(-Math.pow((t-.85)*3,2))*.35);
    },
    dispose(){dead=true;if(word){word.children.forEach(letter=>letter.geometry.dispose());scene.remove(word);}scene.remove(glow);glowMaterial.dispose();glowTexture.dispose();material.dispose();if(title&&title.querySelector('.seer-wordmark')){title.innerHTML=previous;title.removeAttribute('aria-label');}},
  };
}
