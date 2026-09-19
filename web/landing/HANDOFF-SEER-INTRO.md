# Seer entrance and eye-beam animation handoff

The user explicitly requested a THIRD tmux pane to the right, with a new coding
session owning a very animated entrance for the Sentry Seer mascot, eye scanning,
and playful eye beams aimed toward telemetry/logging panels. Implement locally.
The phrase "kill it" is visual bug-zapping, NOT permission to delete logs, hide
real errors, change issue states, spend API credits, or fabricate a successful fix.

## Ownership and coordination

- This handoff is from Codex in tmux `htn:4.2` (%35). It will STOP editing Seer
  while you work. It retains landing hero ownership.
- You own `web/pages/seer/seer.js`, additional presentation-only modules in that
  directory, and Seer-specific test harnesses. User explicitly expanded scope to
  this character, superseding the old landing-only ownership restriction for it.
- Claude in left pane `htn:4.1` (%25) owns API/dashboard/telemetry integration.
  Do not edit `web/pages/telemetry.js`, server/API files or data logic without
  coordinating. Read them to understand real hooks and DOM targets.
- Many unrelated local edits belong to other sessions. Preserve all of them.
- NO commits and NO pushes. User explicitly requires approval before publishing.
  Latest pushed commit predates much of the character refinement.
- Existing server `http://127.0.0.1:8000/`: NEVER stop/restart it. User reloads live.
- No new Meshy/OpenAI/Sentry generation or debugging API calls or paid services.
- Use apply_patch, save small working increments, deterministic eased motion,
  no Math.random. Respect reduced motion, hidden-tab and offscreen pause.

## Read before editing

1. This file in full.
2. `docs/26-seer-embodied.md` from repository root. Honest states are essential.
3. Entire current `web/pages/seer/seer.js` and its dev harness.
4. `web/pages/telemetry.js` and telemetry.html read-only to understand host hooks.
5. Capture the CURRENT page and LOOK at it. Do not judge by code alone.

## Current visual design: preserve it

User approved the current body and has repeatedly refined tiny details. Do not
redesign the character or apply the landing's monochrome manga pass.

- Pink/purple pyramid with curved bottom AND bowed rounded left/right sides.
- Reference width/height approximately 1.33 (455 x 340 source pixels).
- Pale wide eye with a dark purple pupil low in the opening, small catchlight.
  NO cream starburst. Earlier blog references used a different character variant.
- Actual 3D rear apex, visible side planes and subtle three-quarter posture.
- Eight flexible noodle arms. Same pink as the hands, no cuff/glove division.
- Continuous tapered wrist-to-palm geometry, fingers are now longer and each is
  ONE dynamically deformed tube driven by MCP/PIP joints, not capsule pieces.
- Sparse pink/violet pixel grain, concentrated toward edges and gradient/shadow
  transitions. User rejected dense opaque WHITE grain. Do not bring it back.
- Fine ~0.7px dark silhouette outline. Existing instanced and dynamic meshes.
- Magnifier, keyboard, small illustrative floating signal panels. Their animation
  is decorative, NOT fake live telemetry.

Authoritative photo used for this version:
`/tmp/sentry-page-illustration.jpg`
Original URL embedded in user's saved product page:
https://sentry.io/astro-assets/images/products/Adjustred-Ratio_Seer-Illustration.jpg
`/Users/danielwliu/Downloads/sentry` is HTML, not a bitmap. Earlier local images in
seer/reference show a DIFFERENT starburst variant; do not copy that face again.

## Desired sequence

Build a lively, deliberate entrance on `/telemetry`: anticipate, emerge/float in,
unfurl staggered arms, open the eye, scan the scene, catch a target, then settle
into the existing continuous playful idle choreography. Keep the eye and limbs
fluid; no long loading pause or frozen pose. Intro must not delay reading data.

Add eye-beam scans and playful bug-zapping effects aimed at actual on-page
telemetry/logging targets where practical. Beam source must follow the actual
transformed eye, and target must follow its DOM position on resize/scroll.
If effects extend outside the header, isolate a pointer-events:none presentation
overlay. Never obscure data for long, intercept clicks, delete entries or change
real issue status. Do not automatically run Seer, incur credit spend or claim a
fix. Actual `react('thinking'/'verdict'/'stumped')` calls retain their meaning.

## Existing implementation and measurements

- `mountSeer(canvas)` returns react, lookAt, pause, resume, dispose, _debug.
- State set: idle, summoned, thinking, verdict, stumped. Host invokes real states.
- Current header is ~42vh. Eye beam is currently confined to header canvas.
- Idle has a 9-second loop inspecting/presenting tiny decorative signal panels.
- Pause/resume/offscreen/visibility are implemented. Keep these functioning.
- Latest: 21 draw calls idle, 22 beam-active; ~34,178 triangles after replacing
  segmented finger geometry. No page errors. 471 sampled frames passed arm
  attachment checks (max gap ~0.000061 screen units), pause and resume.
- Finger geometry is dynamically skinned manually to actual joint matrices.
  All eight wrists/root connections must remain exact through any entrance.
- Much dead opt-in Meshy-hand code remains. Do not enable generated gloves.

## Verification

From `web/landing/`:

    node tools/dev/seer.mjs /tmp/seer-intro
    node tools/dev/seer-rig.mjs

These use Puppeteer via the local blender-to-threejs package and system Chrome.
seer.mjs captures actual telemetry and harness idle/thinking/stumped/verdict,
mobile. Rig tool asserts eight valid hands, finite attachments, <0.05 root/wrist
gaps, no page errors, and pause/resume. Add timed entrance/beam captures and
sample motion, then LOOK at the resulting images. Test desktop and mobile.
Do not kill other Chrome processes. Run browser tests sequentially when possible.

## Landing context (not your files)

Landing is now non-scrolling. ENTER opens `/telemetry`, INFO opens `/?info`.
INFO uses ENTER's traced Katie Roze lettering, no backing rectangle. Cameras
look at INFO on hover. Information view hides the hero and starts no WebGL scene.
These are LOCAL edits. Don't revert them or commit them along with your work.

Start with a short plan, then implement. Keep user informed in the new pane.
