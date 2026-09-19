# YOUR JOB — cloud / deployment

You own `scripts/`, deployment config, and `.env` plumbing. Do NOT edit `elastic/`,
`web/`, `perception/`, `robot/` — other sessions own those.

Read `../docs/19-deployment.md` and `../docs/18-sentry.md`.

The governing constraint: **the capture→perception→git→Rerun→robot path must work with
NO internet.** Venue wifi is risk R1. Only the web tier goes to AWS.

Build:
1. `scripts/bootstrap_laptop.sh` — venv, deps, **pre-download model weights**, .env check.
2. Sentry init for Python (Pi + laptop + web): `traces_sample_rate=1.0`,
   `profiles_sample_rate=1.0`, `enable_logs=True`, `server_name` per machine, and tag
   `capture_id` / `commit_sha` / `camera` on every span.
3. `SENTRY_STORY.md` at the repo root — append + screenshot EVERY time observability
   tells us something we did not know. This file is the Sentry submission; the SDK is
   just how we generate it.
4. AWS t4g.small for `web/` only, TLS via Caddy, S3 bucket with a 7-day lifecycle rule.
5. `scripts/snapshot.sh` — tag a known-good `room.git` + save the Rerun `.rrd`.

Do NOT put the heavy models on EC2. Use Baseten — purpose-built, sponsor credits, and
it wins a prize. An EC2 GPU means quota requests and driver fights at 2am.
