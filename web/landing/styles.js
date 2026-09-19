// styles.js — two styles ported from the downloaded MicroGhost .blend
// node setups (extracted headlessly; see reference/*.nodes.json):
//
// PAINTERLY (MicroGhost_Painterly_Shader_1.1): the whole look is a
//   hand-painted watercolor-stroke NORMAL MAP driven hard (Normal Map
//   strength 5.0 in the node group) into a Principled BSDF — the strokes
//   live in the LIGHTING. Plus: voronoi-jittered UVs so strokes don't
//   repeat, a second sample at another scale, an animated "boil"
//   (MG Animation Tools steps the UV offset), and an inverted-hull
//   outline ("Painterly Outline" material).
//
// MANGA (Microghost_Manga_Shader_V1.2): screen-space post — luminance
//   tone bands; deep shadows get their crosshatch texture, midtones get
//   their halftone dots, paper texture underneath, ink outlines.
//   Uses the textures unpacked from the .blend.

import * as THREE from 'three';

const loader = new THREE.TextureLoader();

function tex(url, { srgb = false, repeat = true } = {}) {
  const t = loader.load(url);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

// ===================== PAINTERLY (material-based) =====================

export function makePainterlyStyle(sceneRoot) {
  const normalTex = tex('./textures/watercolor_normal.png');
  const outlines = new THREE.Group();
  outlines.name = 'painterlyOutlines';
  const saved = [];   // [{mesh, material}]

  function apply() {
    sceneRoot.traverse((o) => {
      if (!o.isMesh || o.userData.isOutline || o.userData.noStyle) return;
      saved.push({ mesh: o, material: o.material });
      const src = o.material;
      const m = src.clone();
      // blender Normal Map strength 5 ≈ heavy normalScale; two scales
      // blended in-shader below
      m.normalMap = normalTex;   // enables the tangent/normal chunks
      m.normalScale = new THREE.Vector2(1.9, 1.9);
      m.flatShading = false;     // some GLBs ship flatShading:true
      m.roughness = 0.88;   // gouache is matte — specular kills the look
      // raw GLB vertex units are arbitrary (Meshy exports are tiny) —
      // normalize the planar projection by the mesh's own bounds or the
      // strokes sample a single texel and vanish
      o.geometry.computeBoundingBox();
      const bb = o.geometry.boundingBox.getSize(new THREE.Vector3());
      const posScale = 2.0 / Math.max(bb.x, bb.y, bb.z, 1e-6);
      m.onBeforeCompile = (shader) => {
        shader.uniforms.uBoil = { value: 0 };
        shader.uniforms.uPosScale = { value: posScale };
        m.userData.shader = shader;
        // OBJECT-SPACE planar stroke UVs — the .blend samples via its
        // Texture Coordinate node, NOT the mesh UVs. GLB atlases are
        // fragmented islands; strokes through them are invisible.
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>',
            '#include <common>\nvarying vec3 vObjPos;\nvarying vec3 vObjNrm;')
          .replace('#include <begin_vertex>',
            '#include <begin_vertex>\nvObjPos = position;\nvObjNrm = normal;');
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>',
            '#include <common>\nuniform float uBoil;\nuniform float uPosScale;\nuniform mat3 normalMatrix;\nvarying vec3 vObjPos;\nvarying vec3 vObjNrm;')
          .replace('#include <normal_fragment_maps>', `
            // TRIPLANAR stroke perturbation (UDN swizzle): one planar
            // projection stretched edge-on and tilted whole regions dark;
            // three axis projections blended by facing never stretch.
            vec2 boil = vec2(uBoil * 0.37, uBoil * 0.21);
            vec3 op = vObjPos * uPosScale;
            float f = 1.7;   // big loose strokes — high freq reads as noise
            vec3 sx = texture2D( normalMap, op.zy * f + boil ).xyz * 2.0 - 1.0;
            vec3 sy = texture2D( normalMap, op.xz * f + 0.31 - boil ).xyz * 2.0 - 1.0;
            vec3 sz = texture2D( normalMap, op.xy * f + 0.67 + boil ).xyz * 2.0 - 1.0;
            vec3 tw = pow(abs(normalize(vObjNrm)), vec3(4.0));
            tw /= (tw.x + tw.y + tw.z);
            vec3 dObj = vec3(0.0, sx.y, sx.x) * tw.x
                      + vec3(sy.x, 0.0, sy.y) * tw.y
                      + vec3(sz.x, sz.y, 0.0) * tw.z;
            normal = normalize( normal + normalMatrix * dObj * normalScale.x );
            // PAINTED PIGMENT: the .blend's ramps shift the COLOR per
            // stroke, not just the lighting — on a flat saturated albedo
            // the normal trick alone still reads as CG. Strokes push the
            // pigment toward a light warm tint / dark cool tint, plus a
            // low-frequency wash for big watercolor blotches.
            float stroke = clamp(0.5 + (dObj.x + dObj.y + dObj.z) * 1.0, 0.0, 1.0);
            vec3 wx2 = texture2D( normalMap, op.zy * 0.85 + 0.13 ).xyz;
            vec3 wy2 = texture2D( normalMap, op.xz * 0.85 + 0.57 ).xyz;
            vec3 wz2 = texture2D( normalMap, op.xy * 0.85 + 0.71 ).xyz;
            vec2 washV = (wx2 * tw.x + wy2 * tw.y + wz2 * tw.z).xy - 0.5;
            float wash = clamp(0.5 + (washV.x + washV.y) * 2.0, 0.0, 1.0);
            // gouache pigment is never fully saturated — pull toward grey
            // a touch before the stroke tints, or reds go neon
            vec3 pigBase = mix(diffuseColor.rgb,
              vec3(dot(diffuseColor.rgb, vec3(0.299, 0.587, 0.114))), 0.22);
            vec3 pigLight = pigBase * vec3(1.30, 1.20, 0.98) + 0.02;
            vec3 pigDark  = pigBase * vec3(0.74, 0.62, 0.72);
            float pig = clamp(stroke * 0.65 + wash * 0.55, 0.0, 1.0);
            diffuseColor.rgb = mix(pigDark, pigLight, pig);
          `);
      };
      o.material = m;

      // inverted-hull outline — the .blend's "Painterly Outline" is NOT
      // black ink: noise-driven color ramp (deep red -> complementary),
      // alpha 0.64, noise scale 8 — a loose painted underdrawing. The
      // hull edge also wobbles slightly so the line feels hand-pulled.
      const hull = new THREE.Mesh(o.geometry, new THREE.ShaderMaterial({
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false,
        uniforms: { uPosScale: { value: posScale } },
        vertexShader: `
          uniform float uPosScale;
          varying vec3 vOp;
          float h3(vec3 p){
            return fract(sin(dot(p, vec3(12.99, 78.23, 37.72))) * 43758.5);
          }
          float n3(vec3 p){
            vec3 i = floor(p), f = fract(p);
            f = f * f * (3.0 - 2.0 * f);
            float a = mix(h3(i), h3(i + vec3(1,0,0)), f.x);
            float b = mix(h3(i + vec3(0,1,0)), h3(i + vec3(1,1,0)), f.x);
            float c = mix(h3(i + vec3(0,0,1)), h3(i + vec3(1,0,1)), f.x);
            float d = mix(h3(i + vec3(0,1,1)), h3(i + vec3(1,1,1)), f.x);
            return mix(mix(a, b, f.y), mix(c, d, f.y), f.z);
          }
          void main(){
            vOp = position * uPosScale;
            // wobbly line width (hand-pulled edge) — CONTINUOUS noise; a
            // stepped floor() hash makes the hull jump at cell borders and
            // the steps poke through the surface as grid lines
            float w = 0.014 + 0.010 * n3(vOp * 4.0);
            vec3 p = position + normal * w / uPosScale * 0.5;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
          }`,
        fragmentShader: `
          varying vec3 vOp;
          float h3(vec3 p){
            return fract(sin(dot(p, vec3(12.99, 78.23, 37.72))) * 43758.5);
          }
          float n3(vec3 p){
            vec3 i = floor(p), f = fract(p);
            f = f * f * (3.0 - 2.0 * f);
            float a = mix(h3(i), h3(i + vec3(1,0,0)), f.x);
            float b = mix(h3(i + vec3(0,1,0)), h3(i + vec3(1,1,0)), f.x);
            float c = mix(h3(i + vec3(0,0,1)), h3(i + vec3(1,0,1)), f.x);
            float d = mix(h3(i + vec3(0,1,1)), h3(i + vec3(1,1,1)), f.x);
            return mix(mix(a, b, f.y), mix(c, d, f.y), f.z);
          }
          void main(){
            float t = n3(vOp * 8.0);            // their Noise Scale 8
            // their Color Ramp: deep red -> olive/complement
            vec3 col = mix(vec3(0.50, 0.0014, 0.0005),
                           vec3(0.478, 0.50, 0.0), t);
            float a = 0.643 * smoothstep(0.25, 0.6, n3(vOp * 8.0 + 31.7));
            if(a < 0.03) discard;
            gl_FragColor = vec4(col, a);
          }`,
      }));
      hull.userData.isOutline = true;
      hull.scale.setScalar(1.0);   // width comes from the vertex wobble
      hull.visible = !new URLSearchParams(location.search).has('nohull');
      o.add(hull);
      outlines.children.push(hull);   // track for removal
      saved[saved.length - 1].hull = hull;
    });
  }

  function remove() {
    for (const s of saved) {
      s.mesh.material = s.material;
      if (s.hull) s.mesh.remove(s.hull);
    }
    saved.length = 0;
  }

  function update() {}   // boil off — static strokes for now

  return { apply, remove, update };
}

// ======================= MANGA (post-process) =========================

const MANGA_FRAG = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tScene;
  uniform sampler2D tHalftone;
  uniform sampler2D tHatch;
  uniform sampler2D tPaper;
  uniform vec2 uRes;
  uniform float uMix;
  uniform float uBW;     // 1 = kill the color wash entirely
  uniform float uGrit;   // 1 = heavy blacks, crosshatch, grain
  out vec4 outColor;

  float lumAt(vec2 uv){
    vec3 c = texture(tScene, uv).rgb;
    return dot(c, vec3(0.299, 0.587, 0.114));
  }

  void main(){
    vec3 scene = texture(tScene, vUv).rgb;
    float lum = lumAt(vUv);

    // ---- ink outlines: sobel on luminance ----
    vec2 px = 1.0 / uRes;
    float tl = lumAt(vUv + px * vec2(-1.,  1.));
    float tt = lumAt(vUv + px * vec2( 0.,  1.));
    float tr = lumAt(vUv + px * vec2( 1.,  1.));
    float ll = lumAt(vUv + px * vec2(-1.,  0.));
    float rr = lumAt(vUv + px * vec2( 1.,  0.));
    float bl = lumAt(vUv + px * vec2(-1., -1.));
    float bb = lumAt(vUv + px * vec2( 0., -1.));
    float br = lumAt(vUv + px * vec2( 1., -1.));
    float gx = tr + 2.0*rr + br - tl - 2.0*ll - bl;
    float gy = tl + 2.0*tt + tr - bl - 2.0*bb - br;
    // gritty: catch more edges, thicker ink
    float edge = smoothstep(mix(0.22, 0.13, uGrit), mix(0.6, 0.42, uGrit),
                            sqrt(gx*gx + gy*gy));

    // ---- screen-space tiled textures from the .blend ----
    vec2 suv = gl_FragCoord.xy / uRes;
    float aspect = uRes.x / uRes.y;
    vec2 tuv = vec2(suv.x * aspect, suv.y);
    float dots  = texture(tHalftone, tuv * 6.0).r;
    float hatch = texture(tHatch,    tuv * 2.2).r;
    // gritty: second rotated hatch layer -> true crosshatching in shadows
    float hatch2 = texture(tHatch, vec2(tuv.y, -tuv.x) * 2.9).r;
    hatch = mix(hatch, min(hatch, hatch2), uGrit);
    // 0..1, no tiling: the paper scan has a border baked into its edges
    vec3  paper = texture(tPaper,    suv * 0.98 + 0.01).rgb;

    // ---- tone separation (manga bands) ----
    // gamma-lift first: the 3D scene is darker than a manga page.
    // gritty: bands bite deeper, spot blacks go nearly solid
    float L = pow(lum, mix(0.55, 0.62, uGrit));
    float tone;
    if(L > mix(0.78, 0.83, uGrit))      tone = 1.0;
    else if(L > mix(0.52, 0.56, uGrit))
      tone = (dots < smoothstep(0.52, 0.80, L)) ? 1.0 : mix(0.2, 0.10, uGrit);
    else if(L > mix(0.30, 0.34, uGrit))
      tone = (hatch > 0.55 - (L - 0.30) * 1.8) ? 0.9 : mix(0.12, 0.05, uGrit);
    else              tone = mix(0.14, 0.03, uGrit);

    vec3 ink = vec3(0.07, 0.06, 0.08);
    vec3 col = mix(ink, paper, tone);
    // faint color wash keeps the subject readable — killed entirely in BW
    vec3 tint = normalize(texture(tScene, vUv).rgb + 0.02) * 1.1;
    col *= mix(vec3(1.0), tint, 0.35 * (1.0 - uBW));
    col = mix(col, ink, edge);          // outlines on top
    // gritty film grain, biased into the midtones
    float grain = fract(sin(dot(floor(gl_FragCoord.xy),
                                vec2(12.9898, 78.233))) * 43758.5453);
    col *= 1.0 - uGrit * 0.14 * grain * (1.0 - abs(tone - 0.5) * 1.2);

    // alpha passthrough: lets a transparent-background scene composite as
    // a CUTOUT (only the styled objects, no page behind them)
    float alpha = texture(tScene, vUv).a;
    outColor = vec4(mix(scene, col, uMix), alpha);
  }`;

export class MangaPass {
  constructor(renderer, { bw = 0, grit = 0 } = {}) {
    this.renderer = renderer;
    this.mix = 1.0;
    this.bw = bw;
    this.grit = grit;
    this.fsqCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.fsqScene = new THREE.Scene();
    this.mat = new THREE.ShaderMaterial({
      vertexShader: `out vec2 vUv; void main(){ vUv = uv;
        gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: MANGA_FRAG,
      glslVersion: THREE.GLSL3,
      depthTest: false, depthWrite: false,
      uniforms: {
        tScene: { value: null },
        tHalftone: { value: tex('./textures/halftone.png') },
        tHatch: { value: tex('./textures/crosshatch.png') },
        tPaper: { value: tex('./textures/paper.png', { srgb: true }) },
        uRes: { value: new THREE.Vector2() },
        uMix: { value: 1 },
        uBW: { value: bw },
        uGrit: { value: grit },
      },
    });
    this.mat.blending = THREE.NoBlending;   // write rgba as-is (cutout alpha)
    this.fsqScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.mat));
    this.rtScene = new THREE.WebGLRenderTarget(2, 2, {
      minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter,
      type: THREE.HalfFloatType, depthBuffer: true,
    });
  }
  setSize(w, h) {
    this.rtScene.setSize(Math.round(w), Math.round(h));
    this.mat.uniforms.uRes.value.set(Math.round(w), Math.round(h));
  }
  render(scene, camera, target = null) {
    const r = this.renderer;
    r.setRenderTarget(this.rtScene);
    r.render(scene, camera);
    this.mat.uniforms.tScene.value = this.rtScene.texture;
    this.mat.uniforms.uMix.value = this.mix;
    r.setRenderTarget(target);   // null = screen; RT = compositing input
    r.render(this.fsqScene, this.fsqCam);
    r.setRenderTarget(null);
  }
}
