// styles2.js — PAINTERLY V2: "wet paint" fork of the v1 MicroGhost port.
// v1 (styles.js) stays frozen. v2 pushes toward drawing/painting and away
// from realism, using techniques from the watercolor-NPR literature
// (Montesdeoca et al. / artineering "Flair" watercolor style; the inkwash
// write-up; classic Blender posterize-ramp painterly setups):
//
//   * Beer-Lambert pigment compositing: color = paper * exp(-density) —
//     overlapping washes MULTIPLY like real pigment instead of averaging.
//   * Edge darkening: density *= 1 + 1.35*|grad| — pigment migrating to a
//     drying wash's boundary; the most recognizable watercolor signature.
//   * Bleed with chromatographic separation: each channel spreads a
//     different distance (red runs farthest, blue lags).
//   * Granulation: noise modulates density ONLY where pigment exists.
//   * Paper distortion: the whole image is drawn on wobbly fiber.
//   * Posterized tone bands with noise-dithered thresholds (paint blobs).

import * as THREE from 'three';

const loader = new THREE.TextureLoader();

function tex(url, { srgb = false, repeat = true } = {}) {
  const t = loader.load(url);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

// ================= PAINTERLY V2 material (chunkier paint) ================

export function makePainterlyStyle2(sceneRoot) {
  const normalTex = tex('./textures/watercolor_normal.png');
  // 3-step cel ramp: CARTOON lighting — flat bands instead of smooth
  // falloff. The stroke normal-map wobbles the band boundaries so each
  // cel edge reads as a painted blob, not a vector-clean toon line.
  const gradTex = new THREE.DataTexture(
    new Uint8Array([45, 140, 220, 255]), 4, 1, THREE.RedFormat);
  gradTex.minFilter = gradTex.magFilter = THREE.NearestFilter;
  gradTex.needsUpdate = true;
  const saved = [];

  function apply() {
    sceneRoot.traverse((o) => {
      if (!o.isMesh || o.userData.isOutline || o.userData.noStyle) return;
      saved.push({ mesh: o, material: o.material });
      const src = o.material;
      const m = new THREE.MeshToonMaterial({
        color: src.color ? src.color.clone() : new THREE.Color(1, 1, 1),
        vertexColors: !!src.vertexColors,
        map: src.map || null,      // keep textures (tree foliage lives there)
        gradientMap: gradTex,
      });
      m.normalMap = normalTex;
      // strong: strokes shove areas across cel boundaries -> painted
      // blob-shaped cels instead of clean toon bands
      m.normalScale = new THREE.Vector2(2.3, 2.3);
      m.flatShading = false;
      o.geometry.computeBoundingBox();
      const bb = o.geometry.boundingBox.getSize(new THREE.Vector3());
      const posScale = 2.0 / Math.max(bb.x, bb.y, bb.z, 1e-6);
      // force a fresh program per mesh: identical onBeforeCompile strings
      // hash to the same cache key, the cached program is reused, and the
      // reusing material never gets its custom uniforms — uPosScale lands
      // at 0 and every stroke/pigment sample collapses to a constant
      m.customProgramCacheKey = () => 'painterly2:' + o.uuid;
      m.onBeforeCompile = (shader) => {
        shader.uniforms.uBoil = { value: 0 };
        shader.uniforms.uPosScale = { value: posScale };
        m.userData.shader = shader;
        shader.vertexShader = shader.vertexShader
          .replace('#include <common>',
            '#include <common>\nvarying vec3 vObjPos;\nvarying vec3 vObjNrm;')
          .replace('#include <begin_vertex>',
            '#include <begin_vertex>\nvObjPos = position;\nvObjNrm = normal;');
        shader.fragmentShader = shader.fragmentShader
          .replace('#include <common>',
            '#include <common>\nuniform float uBoil;\nuniform float uPosScale;\nuniform mat3 normalMatrix;\nvarying vec3 vObjPos;\nvarying vec3 vObjNrm;')
          .replace('#include <normal_fragment_maps>', `
            vec2 boil = vec2(uBoil * 0.37, uBoil * 0.21);
            vec3 op = vObjPos * uPosScale;
            float f = 1.25;   // even bigger, looser strokes than v1
            vec3 sx = texture2D( normalMap, op.zy * f + boil ).xyz * 2.0 - 1.0;
            vec3 sy = texture2D( normalMap, op.xz * f + 0.31 - boil ).xyz * 2.0 - 1.0;
            vec3 sz = texture2D( normalMap, op.xy * f + 0.67 + boil ).xyz * 2.0 - 1.0;
            vec3 tw = pow(abs(normalize(vObjNrm)), vec3(4.0));
            tw /= (tw.x + tw.y + tw.z);
            vec3 dObj = vec3(0.0, sx.y, sx.x) * tw.x
                      + vec3(sy.x, 0.0, sy.y) * tw.y
                      + vec3(sz.x, sz.y, 0.0) * tw.z;
            normal = normalize( normal + normalMatrix * dObj * normalScale.x );
            // pigment: POSTERIZED into 4 dithered paint zones (the classic
            // Blender ramp-band trick) with more contrast than v1
            float stroke = clamp(0.5 + (dObj.x + dObj.y + dObj.z) * 1.1, 0.0, 1.0);
            vec3 wx2 = texture2D( normalMap, op.zy * 0.6 + 0.13 ).xyz;
            vec3 wy2 = texture2D( normalMap, op.xz * 0.6 + 0.57 ).xyz;
            vec3 wz2 = texture2D( normalMap, op.xy * 0.6 + 0.71 ).xyz;
            vec2 washV = (wx2 * tw.x + wy2 * tw.y + wz2 * tw.z).xy - 0.5;
            float wash = clamp(0.5 + (washV.x + washV.y) * 2.2, 0.0, 1.0);
            float pig = clamp(stroke * 0.6 + wash * 0.6, 0.0, 1.0);
            float dith = (sx.x + sy.y) * 0.22;   // reuse stroke tex as dither
            pig = clamp((floor(pig * 4.0 + dith) + 0.5) / 4.0, 0.0, 1.0);
            // cartoon pigment: KEEP the saturation (no grey pull) — flat
            // bold color zones; light tint warm, dark tint cool-saturated
            vec3 pigBase = diffuseColor.rgb;
            vec3 pigLight = pigBase * vec3(1.42, 1.28, 1.02) + 0.06;
            vec3 pigDark  = pigBase * vec3(0.62, 0.48, 0.70);
            diffuseColor.rgb = mix(pigDark, pigLight, pig);
            // cartoon pigment gamut: pure primaries destroy all form in the
            // watercolor pass (saturated channels clamp flat in the
            // Beer-Lambert step). Real paint is never a pure primary.
            diffuseColor.rgb = clamp(
              mix(diffuseColor.rgb,
                  vec3(dot(diffuseColor.rgb, vec3(0.333))), 0.15),
              vec3(0.10), vec3(0.92));
            // CARTOON CEL painted INTO the pigment: the physical lights
            // (ambient/fill/rim) bypass the toon ramp and wash the shadow
            // cel out — so shade it here, cartoon-style: fixed key
            // direction, stroke-wobbled boundary, COOLER (not just darker)
            // shadow pigment like a real cartoonist would mix.
            // WORLD-space cel key, aligned with the physical key light so
            // the painted shadow and the cast shadow agree. World-keyed =
            // the shadow stays put on the object as the camera orbits
            // (view-keyed looked like the lighting followed the viewer).
            vec3 celKey = normalize(vec3(0.73, 0.68, 0.06));
            vec3 nW = inverseTransformDirection(normal, viewMatrix);
            float cel = dot(nW, celKey) + (dObj.x + dObj.y) * 0.35;
            // cutoff sits IN the lit range: a low cutoff only catches the
            // foreshortened silhouette rim from most angles
            float shadowZone = 1.0 - smoothstep(0.28, 0.52, cel);
            vec3 shadowPig = diffuseColor.rgb * vec3(0.34, 0.30, 0.62)
                           + vec3(0.03, 0.0, 0.09);
            diffuseColor.rgb = mix(diffuseColor.rgb, shadowPig, shadowZone);
          `);
      };
      o.material = m;

      // chunkier hand-drawn hull outline than v1 (wider, more opaque)
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
            float w = 0.024 + 0.016 * n3(vOp * 3.0);
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
            float t = n3(vOp * 6.0);
            // darker inkier line for the cartoon read, still color-noisy
            vec3 col = mix(vec3(0.26, 0.03, 0.02),
                           vec3(0.24, 0.26, 0.04), t);
            // higher coverage: the old threshold left long silhouette
            // stretches with no line at all (read as broken, not sketchy)
            float a = 0.88 * smoothstep(0.08, 0.42, n3(vOp * 6.0 + 31.7));
            if(a < 0.03) discard;
            gl_FragColor = vec4(col, a);
          }`,
      }));
      hull.userData.isOutline = true;
      o.add(hull);
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

  return { apply, remove, update() {} };
}

// ================= WATERCOLOR post pass (the wet stuff) ==================

const WATERCOLOR_FRAG = /* glsl */`
  precision highp float;
  in vec2 vUv;
  uniform sampler2D tScene;
  uniform sampler2D tDepth;
  uniform sampler2D tPaper;
  uniform mat4 uInvPV;        // clip -> world (camera.matrixWorld * projInv)
  uniform vec2 uRes;
  uniform float uMix;
  uniform float uBleed;       // bleed radius, px
  uniform float uLoose;       // gaps&overlaps misregistration, px
  uniform float uPosterize;   // 0..1 band strength
  uniform float uStrength;    // pigment strength
  uniform float uImpact;      // 2 = inverted B&W frame, 1 = normal B&W
  uniform vec2 uFocus;        // contact point (UV) — the burst converges here
  uniform float uFocusOn;     // 1 = grab impact (burst); 0 = plain frames
  uniform float uAfter;       // lingering afterimage veil, decays post-hit
  out vec4 outColor;

  float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float vnoise(vec2 p){
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h21(i), h21(i + vec2(1,0)), f.x),
               mix(h21(i + vec2(0,1)), h21(i + vec2(1,1)), f.x), f.y);
  }
  float fbm(vec2 p){
    return vnoise(p) * 0.55 + vnoise(p * 2.13 + 7.7) * 0.28
         + vnoise(p * 4.71 + 3.1) * 0.17;
  }
  vec3 sceneAt(vec2 uv){ return texture(tScene, uv).rgb; }
  float lumAt(vec2 uv){ return dot(sceneAt(uv), vec3(0.299, 0.587, 0.114)); }

  void main(){
    vec2 px = 1.0 / uRes;

    // WORLD ANCHOR: reconstruct each pixel's 3D position from depth and
    // drive all paint noise from it — paint sticks to the objects instead
    // of shower-dooring across a screen-fixed layer when the camera moves.
    // Far field is distance-compressed so the backdrop gets soft washes,
    // not ultra-fine grain.
    float depth = texture(tDepth, vUv).x;
    vec4 wp4 = uInvPV * vec4(vUv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
    vec3 wp = wp4.xyz / wp4.w;
    vec3 wpc = wp * (4.0 / (4.0 + length(wp)));
    vec2 aa = wpc.xy + vec2(wpc.z * 0.83, wpc.z * 0.31);

    // 1) paper distortion — the drawing sits on wobbly fiber
    vec2 wobble = vec2(fbm(aa * 9.0), fbm(aa * 9.0 + 19.3)) - 0.5;
    vec2 uv0 = vUv + wobble * px * 5.0;

    // 1b) GAPS & OVERLAPS (Montesdeoca et al.): the COLOR layer is
    // misregistered against the edges by low-frequency noise, so washes
    // overflow the shapes on one side and fall short on the other —
    // paint escaping the drawing, like a loosely-registered print.
    // Edge darkening below stays UNOFFSET so the ink holds the form.
    vec2 misreg = (vec2(fbm(aa * 1.9 + 4.2), fbm(aa * 1.9 + 13.1)) - 0.5)
                * px * uLoose;

    // 2) bleed w/ chromatographic separation: red spreads farthest
    //    (inkwash's per-channel bleed rates), taps jittered by noise
    // 4 jittered taps (was 6): visually identical wash, 1/3 less bandwidth
    vec3 bled = vec3(0.0);
    float wsum = 0.0;
    for (int k = 0; k < 4; k++) {
      float fk = float(k);
      vec2 dir = vec2(vnoise(aa * 3.5 + fk * 13.1) - 0.5,
                      vnoise(aa * 3.5 + fk * 7.7 + 31.0) - 0.5);
      float w = 0.5 + 0.5 * vnoise(aa * 5.7 + fk * 3.3);
      vec2 cuv = uv0 + misreg;
      bled += w * vec3(
        sceneAt(cuv + dir * px * uBleed * 1.7).r,
        sceneAt(cuv + dir * px * uBleed * 1.0).g,
        sceneAt(cuv + dir * px * uBleed * 0.5).b);
      wsum += w;
    }
    bled /= wsum;

    // 3) into pigment density (Beer-Lambert domain), with pigment
    // turbulence: big soft unevenness in how much paint was laid down
    vec3 dens = -log(clamp(bled, 0.05, 1.0));
    dens *= 0.82 + 0.36 * fbm(aa * 1.05 + 5.5);

    // 4) posterize density into dithered paint bands — dither must be
    // LOW-frequency (blobby wash borders) or smooth gradients turn to static
    float dith = (fbm(aa * 4.2) - 0.5) * 0.55;
    vec3 q = (floor(dens * 2.6 + dith) + 0.5) / 2.6;
    dens = mix(dens, q, uPosterize);

    // 5) edge darkening: pigment pools at wash boundaries (screen-space is
    // correct here — it tracks the image's own edges)
    float gx = lumAt(uv0 + px * vec2( 1.6, 0.0)) - lumAt(uv0 - px * vec2(1.6, 0.0));
    float gy = lumAt(uv0 + px * vec2( 0.0, 1.6)) - lumAt(uv0 - px * vec2(0.0, 1.6));
    float edge = smoothstep(0.05, 0.5, length(vec2(gx, gy)));
    dens *= 1.0 + 0.9 * edge;

    // 6) granulation — CLUMPY, like pigment settling into paper valleys,
    // anchored to the world like everything else
    float meanDens = dot(dens, vec3(0.3333));
    // most visible in MID washes: fade out in blank paper AND in darks
    float presence = smoothstep(0.08, 0.6, meanDens)
                   * (1.0 - smoothstep(1.4, 2.4, meanDens));
    float gclump = vnoise(aa * 13.0) * vnoise(aa * 22.0 + 17.0);
    dens *= 1.0 + (gclump - 0.28) * 0.34 * presence;

    // 7) compose on paper
    vec3 paper = texture(tPaper, vUv * 0.98 + 0.01).rgb;
    paper = mix(vec3(0.97, 0.95, 0.90), paper, 0.65);
    vec3 col = paper * exp(-dens * uStrength);

    // IMPACT FRAME (Nakamura-style): 1-2 frames of thick high-contrast
    // B&W at the exact peak of the hit — inverted polarity first, then
    // normal — everything else (ring, dashes, jolt) reads as ink
    if (uImpact > 0.5) {
      float lum = dot(col, vec3(0.299, 0.587, 0.114));
      // FOCUS BURST (the sakuga trick): the impact frame isn't a filter,
      // it's a composition — a white halo core at the contact point and
      // spokes CONVERGING into it, so the eye reads WHERE the force is.
      // Localized: everything fades out by mid-radius.
      // NO radial lines (rejected) — just a soft halo at the contact
      vec2 fv = (vUv - uFocus) * vec2(uRes.x / uRes.y, 1.0);
      float fd = length(fv);
      float core = (1.0 - smoothstep(0.06, 0.16, fd)) * uFocusOn;
      // frame 1, THE FLASH (inverted): the stark flat two-tone poster —
      // with the burst punching WHITE through it at the grab point
      if (uImpact > 1.5) {
        float two = 1.0 - smoothstep(0.58, 0.66, lum);
        two = max(two, core);
        outColor = vec4(vec3(two), 1.0);
        return;
      }
      // held frames: GRITTY MANGA — paper, HALFTONE DOTS in the mids,
      // rough HATCHING in the darks, solid ink, film grain over it all
      vec2 fc = vUv * uRes;
      lum += (h21(fc) - 0.5) * 0.10;                  // grain
      vec3 ink;
      if (lum > 0.78) {
        ink = vec3(0.96);                             // paper
      } else if (lum > 0.45) {
        float dk = (0.78 - lum) / 0.33;               // halftone dots
        float d = sin(fc.x * 0.6) * sin(fc.y * 0.6);
        ink = d > mix(1.0, -1.0, dk) ? vec3(0.04) : vec3(0.96);
      } else if (lum > 0.18) {
        float dk = (0.45 - lum) / 0.27;               // rough hatch
        float s = sin((fc.x + fc.y) * 0.45 + fbm(vUv * 30.0) * 2.5);
        ink = s > mix(1.0, -1.0, dk) ? vec3(0.04) : vec3(0.96);
      } else {
        ink = vec3(0.04);                             // solid ink
      }
      // held frames: just the clean halo at the contact
      if (core > 0.5) ink = vec3(0.97);
      outColor = vec4(ink, 1.0);
      return;
    }
    // AFTERIMAGE: a white veil lingering after the impact frames and
    // decaying out — persistence-of-vision, so the hit doesn't just cut
    // back to color
    col = mix(col, vec3(0.97, 0.95, 0.90), uAfter);
    outColor = vec4(mix(sceneAt(vUv), col, uMix), 1.0);
  }`;

export class WatercolorPass {
  constructor(renderer) {
    this.renderer = renderer;
    this.mix = 1.0;
    this.fsqCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
    this.fsqScene = new THREE.Scene();
    this.mat = new THREE.ShaderMaterial({
      vertexShader: `out vec2 vUv; void main(){ vUv = uv;
        gl_Position = vec4(position.xy, 0.0, 1.0); }`,
      fragmentShader: WATERCOLOR_FRAG,
      glslVersion: THREE.GLSL3,
      depthTest: false, depthWrite: false,
      uniforms: {
        tScene: { value: null },
        tDepth: { value: null },
        tPaper: { value: tex('./textures/paper.png', { srgb: true }) },
        uInvPV: { value: new THREE.Matrix4() },
        uRes: { value: new THREE.Vector2() },
        uMix: { value: 1 },
        uBleed: { value: 8.0 },       // they want the bleed — keep it juicy
        uLoose: { value: 9.0 },       // washes slide loose of the shapes
        uPosterize: { value: 0.3 },   // cels now come from the toon ramp
        uStrength: { value: 0.74 },
        uImpact: { value: 0 },
        uFocus: { value: new THREE.Vector2(0.5, 0.5) },
        uFocusOn: { value: 0 },
        uAfter: { value: 0 },
      },
    });
    this.fsqScene.add(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.mat));
    this.rtScene = new THREE.WebGLRenderTarget(2, 2, {
      minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter,
      type: THREE.HalfFloatType, depthBuffer: true,
    });
    this.rtScene.depthTexture = new THREE.DepthTexture(2, 2);
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
    this.mat.uniforms.tDepth.value = this.rtScene.depthTexture;
    this.mat.uniforms.uInvPV.value.multiplyMatrices(
      camera.matrixWorld, camera.projectionMatrixInverse);
    this.mat.uniforms.uMix.value = this.mix;
    r.setRenderTarget(target);   // null = screen; RT = compositing input
    r.render(this.fsqScene, this.fsqCam);
    r.setRenderTarget(null);
  }
}
