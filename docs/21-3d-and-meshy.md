# 21 — 3D assets, Meshy, and rigging what we actually have

## The headline: Meshy cannot rig a robot arm, and does not need to

Meshy's own API docs say programmatic rigging *"only works well with standard humanoid
(bipedal) assets"* and is *"not suitable for non-humanoid assets."* An SO-101 is not
bipedal. Neither is a Bracket Bot. Sending either through the rigger produces a Smart Rig
result that looks plausible and bends wrong.

**But rigging is the wrong tool anyway.** A skinned mesh exists so that *soft* geometry can
deform — flesh over a joint, cloth over a shoulder. A robot arm has no soft geometry. It is
a chain of **rigid links joined at revolute joints**, which is a hierarchy, not a skin.

The pomme camera arm proves the point. Copied into
[`viz/pomme-arm/`](../viz/pomme-arm/README.md), `snakeArms.js:48`:

```js
function buildChain({ segments, segLen, baseW }) {
  const root = new THREE.Group();
  let parent = root;
  for (let i = 0; i < segments; i++) {
    const j = new THREE.Group();
    j.position.y = i === 0 ? 0 : segLen;   // offset along the previous link
    parent.add(j);                          // nested -> a kinematic chain
    parent = j;
    // rigid parts parented to this joint: knuckle pin, clevis fork, link
    // body, hydraulic piston, cable run, bolt heads, service hose
  }
}
```

675 lines, a real `THREE.PerspectiveCamera` living in the pod, procedural S-curve posing,
unfurl-from-coil entry, idle breathing — and **not one bone, not one skin weight, no Meshy
rig at all.** Posing is `joint.rotation.z = angle`.

---

## What each tool is actually for

| asset | how to make it | rigged? |
|---|---|---|
| **SO-101 arm** | the real open-source CAD — LeRobot ships the meshes | no — nested Groups per joint |
| **Bracket Bot chassis** | primitives, or a simple model | no — two wheel Groups |
| **Table / floor / zones** | a flat disc + a material graph ([rule 25](../viz/pomme-arm/README.md)) | n/a |
| **Props** — mug, hammer, book, cube | **Meshy** — this is exactly its job | **no rigging needed at all** |
| a character, if we ever want one | Meshy mesh → Blender rig | yes, humanoid only |

> **The props are where Meshy earns its keep.** The demo needs 5–6 curated objects that
> look good and are visually distinct. That is a text-to-3D job with no rig, no skin and no
> contract to honour — Meshy's strongest case and its lowest risk.

---

## Use the real SO-101 geometry, not a lookalike

The SO-101 is fully open source and LeRobot ships its meshes. Using the real geometry is
strictly better than modelling an approximation, for a reason specific to this project:

**The web dashboard shows the arm's live pose.** If the 3D model's joint hierarchy matches
the physical arm's kinematics, then feeding it the six servo angles read back from the
Feetech bus draws the arm *exactly as it is standing in the room*. A lookalike model needs
a hand-tuned mapping per joint that will be subtly wrong forever.

So:

```
LeRobot SO-101 meshes  ->  one THREE.Group per joint, nested in kinematic order
                       ->  group[i].rotation[axis] = servo_angle[i]
                       ->  the screen matches the room
```

Joint axes and limits come from the URDF, not from eyeballing. That is a measurement, not
a guess — the same discipline as [`20-perception-logic.md`](20-perception-logic.md).

---

## Prompting Meshy for props

From the portfolio's hard-won notes, which apply unchanged:

- **Shape words first.** For anything that will be posed: T-pose or A-pose, limbs clearly
  separated, hands empty, no props. For our static props: "single object, centred, neutral
  lighting, no base, no pedestal".
- **Negate the drift the subject invites.** Ask for a hammer and you get a fantasy war
  hammer; say "no ornament, plain claw hammer, wooden handle".
- **Keep one style tail** across every prop so the set reads as a set.
- **Low poly, under 100k faces.** Sobel outlines and painterly passes turn dense meshes
  into outline soup.
- **Rig the PREVIEW task directly** if you ever do rig — Meshy's rigger accepts it and
  refine is not required, which saves a paid job.

Our props also have a **physical** constraint the portfolio's did not: they must be
graspable by a 30 kg·cm tabletop arm and visible to stereo. Textured and matte, nothing
white, glossy or transparent ([R3](08-risks.md)).

---

## The 100× trap, for anything that IS Meshy-rigged

Field note 26, and it will cost an hour if it is not known in advance:

> A rigged glTF from Meshy or Mixamo hangs the skinned mesh under a **0.01-scale Armature
> with centimetre bones**, while the geometry itself is in metres. At render time the bones
> reproduce the geometry, so the character's true extent is the **geometry** bounding box.

`Box3.setFromObject` — plain or precise — is therefore **wrong by 100×**. Normalise from
`geometry.boundingBox`, clone with `SkeletonUtils`, and drive bones by rotating about
**world** axes on top of the bind quaternion (`inv(parentWorld) · R · parentWorld · bind`)
so choreography is written in scene terms rather than per-bone axis guesses.

Related, from the same notes: glTF strips dots from bone names (`thigh.L` → `thighL`), so
look up with a fallback and assert loudly in dev — a name miss fails **silently**, the
model renders perfectly and simply never moves.

---

## Where this lands in our build

| priority | what | why |
|---|---|---|
| **T2** | SO-101 from real CAD, joint hierarchy, driven by servo angles | makes the dashboard show the real arm |
| **T2** | 5–6 Meshy props matched to the physical object set | the 3D view matches the table |
| T3 | pomme painterly pass over the viz | looks extraordinary, costs a day, cut it first |
| never | Meshy-rigging the arm | it will not work and it is not needed |

The [`viz/`](../viz/) folder already owns the Rerun blueprint. 3D assets live beside it, and
none of this starts before the core `status` → `diff` → `revert` loop is green.
