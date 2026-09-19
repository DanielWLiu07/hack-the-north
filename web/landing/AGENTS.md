# AGENTS.md — web/landing (the GITRL 3D hero)

Read `HANDOFF-3D.md` in this folder in full before doing anything: it is the complete context —
what the user asked for and in what order, what is built, what is measured, what is broken, which
files are yours, and how to verify. Then `../LANDING-TASK.md` and `../PAGES.md`.

Hard rules:
- You own only the 3D files listed in HANDOFF-3D.md. Do not edit `index.html`, `dash.*`, `graph.*`,
  `sentry.js`, `vendor/`, or anything outside `web/landing/`. Do not stop the server on :8000.
- Never commit unless the user asks; never add AI attribution to anything.
- Motion is deterministic (`mech.hash`, never `Math.random`); every animated value is eased (no
  instant switches); arm roots stay off-screen; nothing covers GITRL or ENTER.
- Verify by looking at captured frames and by sampling motion (`tools/dev/`), not by reading code.
