# pomme-arm — reference, copied from `test-run-3d`

Source: `~/Dev/projects/2026/test-run-3d` (the pomme scene — `styles2.js` is the
painterly source that `blender-to-threejs` ports).

| file | what |
|---|---|
| `snakeArms.js` | **the arm rig, 675 lines.** `buildSpyRig()` → two serpentine arms: a spy-cam arm with a real `THREE.PerspectiveCamera` in the pod, and a claw arm |
| `robotScene.js`, `arm.html`, `rigdiag.html` | the dev harness it was tuned in |
| `styles2.js`, `painterly.js` | pomme's painterly material — the thing the library ports as `painterly-material.ts` |
| `models/camhead2.glb` | the camera pod |
| `models/armseg.glb`, `clawhead.glb` | segment + claw geometry |

Big claw variants (`clawgrip` 31 MB, `clawhand` 22 MB) were left behind — pull them from
`test-run-3d/models/unused/` if needed.

## The pattern worth stealing

```js
function buildChain({ segments, segLen, baseW }) {
  const root = new THREE.Group();
  let parent = root;
  for (let i = 0; i < segments; i++) {
    const j = new THREE.Group();
    j.position.y = i === 0 ? 0 : segLen;   // <- offset along the link
    parent.add(j);                          // <- nested: a kinematic chain
    parent = j;
    const w = baseW * (1 - (i / (segments - 1)) * 0.45);   // taper
    // knuckle pin, clevis fork, link body, piston, cable run, bolts, hose
  }
}
```

**No skinning. No armature. No Meshy rig.** Each joint is a nested `THREE.Group`, each
link is rigid geometry parented to it, and posing is setting `rotation` on the groups.
That is what a robot arm *is*, and it is why the pomme arm works.

Two details worth copying beyond the structure:

- **Links alternate box and cylinder profiles** (`i % 2`) — the comment says it outright:
  *"real arms mix castings and turned sections, identical links read as toy chain."*
- **A front fill light is mandatory** under the manga pass, or camera-facing surfaces
  crush to black and the arm reads as a silhouette blob.
