# 19 — Deployment: what runs where

## The question

*"Spin up an AWS instance for the server instead of my laptop."*

Short answer: **yes, but only for the web tier.** Moving the whole thing to AWS would break the
single most important engineering decision in the project.

## Why not move everything

[`08-risks.md` R1](08-risks.md) says venue wifi is the highest-probability project killer, and
the mitigation is *bring our own router so the robot↔brain link never touches the internet*.
Putting the brain in AWS inverts that:

| what breaks | why |
|---|---|
| **Every capture crosses venue wifi** | 2–5 MB of JPEGs per `POST /capture`, to the internet and back. On congested conference wifi that's 10–30 s instead of 2 s — in the middle of a live demo. |
| **Telemetry crosses venue wifi** | 400 docs/s of it, continuously, all weekend. |
| **Rerun** | the viewer renders locally; processing in AWS means pulling point clouds back down. |
| **Debugging** | SSH to a box at 4am, vs. a laptop you can see. |
| **The demo dies with the wifi** | and at a 1000-person hackathon, it will wobble. |

The critical path — capture → perception → git → Rerun → robot — **must survive with zero
internet**. That's non-negotiable.

## The split that's actually better than either extreme

```
┌─ LOCAL (laptop, our own router, no internet required) ──────────────┐
│  perception · roomctl · room.git · viz (Rerun) · executor · agent    │
│  ← the entire capture→diff→motion critical path lives here          │
└──────────┬──────────────────────────────────────┬───────────────────┘
           │ SQS (outbound poll) · S3 · Sentry    │ HTTPS: telemetry + observations
           │ OpenAI                               │ (~400 docs/s, venue internet)
┌──────────┴─── AWS us-east-1 ──────────┐  ┌──────┴─── GCP us-east4 ─────────┐
│  t4g.small, no GPU                    │  │  Elastic Serverless             │
│  web/ dashboard + API · webhooks      │  │  room-* · robot-telemetry       │
│  public URL · S3 · SQS · SNS          │  │                                 │
│  queries Elasticsearch DIRECTLY ──────┼─►│  cross-cloud, SAME METRO        │
└───────────────────────────────────────┘  └─────────────────────────────────┘
        Northern Virginia  ◄───── a few ms ─────►  Ashburn, Virginia
```

**What the AWS tier buys, genuinely:**

1. **A public URL for the dashboard.** Judges can open it on their phones while standing at
   your table. That is a real demo advantage, and it costs nothing to the critical path.
2. **Webhooks hit it directly.** No `ngrok`, no rotating tunnel URL, no captive-portal
   weirdness. GitHub and Elastic Agent Builder POST straight to a stable address.
3. **It stays up when the laptop sleeps.** The dashboard, search and history keep working
   because they read Elasticsearch directly — the laptop isn't in that path at all.
4. **Sentry Uptime Monitoring gets something real to watch** (see [`18-sentry.md`](18-sentry.md)).

**The key property:** `/api/search`, `/api/history`, `/api/analytics`, `/api/object/{id}` all
query Elasticsearch directly from AWS — across to GCP, see below. **Zero laptop dependency.**
Only `/api/command` and `/api/resolve` need the robot, and those go out via SQS — which the
laptop polls outbound, so still no inbound hole.

## Sizing and setup

| | choice | why |
|---|---|---|
| instance | **t4g.small** (ARM, 2 vCPU, 2 GB) | it's a FastAPI proxy, not a compute node. ~$0.02/hr |
| region | **`us-east-1`, nothing else** | same as the S3 bucket and SQS queue, **and the same metro as Elastic's GCP `us-east4`** — see below |
| TLS | Caddy or nginx + Let's Encrypt | judges opening `http://` on a phone looks bad |
| deploy | `git pull` + `systemd`, or a single Docker container | it's a weekend; don't build a pipeline |
| secrets | ES key + Sentry DSN in the instance env | **no AWS creds needed on the box** — use an IAM role |

**~30 minutes.** Do it once, Friday, and don't touch it again.

## Cross-cloud: Elasticsearch is on GCP `us-east4`, the web tier on AWS `us-east-1`

Our Elastic Serverless project turned out to be hosted on **GCP `us-east4`**, not AWS. Every
`/api/search`, `/api/history` and `/api/analytics` call from the web tier now crosses clouds.
**Keep the web tier on AWS `us-east-1` anyway** — the change is smaller than it sounds:

| concern | what actually changes |
|---|---|
| distance | **Almost nothing.** `us-east4` is Ashburn, Virginia; `us-east-1` is Northern Virginia — the same data-centre metro. Expect a few ms per round trip, not tens. Measure it once (below) rather than trusting this row. |
| network path | **Nothing.** Serverless was always a public HTTPS endpoint behind an API key; we never had a private link to it, even while we assumed AWS. TLS + API key, as before. |
| transfer cost | Responses flow GCP → AWS: AWS ingress is free, and whatever Elastic meters on its side is on KB-sized dashboard queries — cents over a weekend. The heavy stream (laptop telemetry, ~400 docs/s) goes laptop → Elastic over the venue internet whichever cloud Elastic is in. |
| credentials | **Nothing.** The ES API key is the only secret that crosses clouds, and it was already in the instance env. |

**Why not move the web tier to GCP, next to the data?** It would co-locate one hop and break
two. `/api/command` and `/api/resolve` go out through **SQS**, and clouds live in **S3**. On EC2
those use an IAM role, so there are no AWS keys on the box; on a GCP VM they would need
long-lived AWS access keys — exactly what the secrets row above exists to prevent. Porting SQS →
Pub/Sub and S3 → GCS mid-hackathon to save a few milliseconds is a bad trade.

**The one real new constraint: the region must be `us-east-1`.** Before, it was chosen to
match S3/SQS; now it is also what keeps the cross-cloud hop metro-local. A west-coast region
would put roughly 60–70 ms on every Elasticsearch round trip — a visibly slower dashboard
from a one-character typo in the launch wizard.

Measure it once from the instance, warm (the second line is the per-query tax):

```bash
for i in 1 2; do curl -s -o /dev/null -H "Authorization: ApiKey $ELASTIC_API_KEY" \
  -w 'connect %{time_connect}s  tls %{time_appconnect}s  total %{time_total}s\n' "$ELASTIC_URL"; done
```

**When to revisit:** if the Elastic project gets recreated anyway (e.g. moving to a sponsor
org), create it on AWS `us-east-1` and the question disappears. Don't recreate it *for* this —
mappings are immutable and re-ingesting history costs more than the milliseconds are worth.

## Do NOT put the heavy models on EC2

Tempting — SAM 3 and the VLM are slow on a laptop CPU. But a GPU instance means quota
requests, driver setup, and an AMI you'll fight at 2am.

**Use Baseten instead.** It's purpose-built for exactly this, it's a sponsor with credits at
the booth, and it wins a prize. Strictly better than an EC2 GPU on every axis that matters
here. See [`07-prizes.md`](07-prizes.md).

## What lives where, complete

| component | home | may the internet be down? |
|---|---|---|
| Pi: capture, arm, nav, telemetry | **robot** | yes — local network only |
| perception, roomctl, room.git, executor, Rerun | **laptop** | **yes — must keep working** |
| agent loop | laptop | needs OpenAI, so no — but its ES tools degrade gracefully |
| `web/` dashboard + API + webhooks | **as built: GCP `e2-small` us-east4 (backend) + Vercel (frontend)**, plus the laptop's own copy on :8000 (see "As built" above; the AWS plan was never deployed) | no — and that's fine, it's not the critical path |
| Elasticsearch | Elastic Serverless, **GCP `us-east4`** | no — and the web tier reaches it cross-cloud, same metro |
| heavy models (SAM 3, VLM) | **Baseten** | no — fall back to local YOLO if it drops |
| point-cloud blobs | **local only** as built (no AWS keys, nothing uploads to S3) | yes |

**Design rule:** anything the demo cannot survive losing runs on the local network. Everything
else can live in the cloud.

## A stable public URL: a named Cloudflare tunnel on `repr.ink`

The quick tunnel (`*.trycloudflare.com`) gets a new random URL every time `cloudflared`
restarts, so it can't go on a Devpost submission. A **named** tunnel keeps one hostname we own,
reads a config file, and runs under launchd, so it survives restarts and logins. It's outbound
only: no port forwarding and nothing inbound on venue wifi (R1). Primary domain **`repr.ink`**,
fallback **`gitirl.ink`**.

**Honest limit:** it still serves from the laptop, so a sleeping laptop is down, and Sentry
Uptime will say so. Run `caffeinate -dimsu &` through judging. Moving to AWS later
(`deploy_web.sh`) doesn't change the URL: run the same tunnel on the box (copy
`~/.cloudflared/<UUID>.json` and `gitspace.yml`) and the hostname follows it.

### The only manual steps (nothing to buy beyond the domain; Cloudflare's Free plan is enough)

1. **Register `repr.ink`** (or `gitirl.ink`). If Cloudflare Registrar sells `.ink`, register it
   there: its nameservers are already Cloudflare's, so skip step 3.
2. **Cloudflare dashboard → Add a site → `repr.ink` → Free plan.** A fresh domain imports no
   records; if it does, delete them.
3. **At the registrar, replace the nameservers** with the two Cloudflare assigns
   (`<name>.ns.cloudflare.com`). Wait for the site to show **Active**. Check with
   `dig +short NS repr.ink @1.1.1.1`, which should print the two Cloudflare names.
4. **`cloudflared tunnel login`** opens a browser; pick `repr.ink`. That writes
   `~/.cloudflared/cert.pem`.

Then everything else is one command: **`scripts/named_tunnel.sh repr.ink`** (or
`repr.ink gitirl.ink` to serve both). It creates the tunnel `gitspace`, writes the config,
adds the DNS records, installs the launchd agent, checks `https://repr.ink/api/health` for a
200, re-points Sentry Uptime at it, and sets `WEB_PUBLIC_URL`. It's safe to re-run.
`--status` and `--stop` do what they say.

### The DNS records it creates (per domain; the same for `gitirl.ink`)

| type | name | target | proxy |
|---|---|---|---|
| `CNAME` | `@` (`repr.ink`) | `<TUNNEL-UUID>.cfargotunnel.com` | **Proxied** (orange cloud) |
| `CNAME` | `www` | `<TUNNEL-UUID>.cfargotunnel.com` | **Proxied** |

These **only work proxied, on a zone that uses Cloudflare's nameservers**. `cfargotunnel.com`
names don't resolve publicly, so the same CNAME at another DNS host silently fails. The apex
CNAME is legal because Cloudflare flattens it. There are no `A`/`AAAA` records and no ports.

### The config (`~/.cloudflared/gitspace.yml`, written by the script; never in the repo)

```yaml
tunnel: <TUNNEL-UUID>
credentials-file: /Users/<you>/.cloudflared/<TUNNEL-UUID>.json   # a secret
originRequest:
  connectTimeout: 10s
  keepAliveTimeout: 90s          # /api/events is SSE: long-lived responses
ingress:
  - hostname: repr.ink
    service: http://127.0.0.1:8000
  - hostname: www.repr.ink
    service: http://127.0.0.1:8000
  - service: http_status:404      # any other hostname pointed at us gets nothing
```

Checked offline with `cloudflared tunnel ingress validate` (OK), and with `ingress rule`:
`repr.ink` and `www.gitirl.ink` route to the local web server, and an unknown host gets a 404.

## As built, 2026-09-19 07:50Z: backend on GCP, frontend on Vercel

**Public URL: `https://gitspace-five.vercel.app`** (Sentry Uptime #10384065 watches `/api/health` on it).

```
browser ──► Vercel CDN  gitspace-five.vercel.app      static: landing/ + pages/ assets (76 files, 21 MB)
               │ everything else (rewrites: filesystem first, then proxy)
               ▼
            GCP us-east4-a  gitspace-web (e2-small)   Caddy + Let's Encrypt: https://8-234-158-138.sslip.io
               web/server.py under systemd, .venv    /api/*, SSE /api/events, /object/<id>, /capture, /replay
               room.git mirror  ◄── laptop hooks      Andrew's parser (gitirl b4f3e07) for the agent panel
               │ same metro, a few ms
               ▼
            Elastic Serverless  GCP us-east4
```

**Why GCP after all.** The section above chose AWS so SQS and S3 could run without keys on the
box. Nothing in the code uses SQS or S3, and the AWS keys were never filled in, so that reason is
gone. GCP `us-east4` is the same region as Elastic. **The laptop keeps the whole critical path**
(capture → perception → roomctl → room.git → robot). The cloud tier is a mirror:
- **room.git:** hooks in the laptop's room.git (post-commit/checkout/merge/rewrite) push a
  bundle in the background, coalesced, never blocking git (`scripts/gcp_mirror.sh sync-bg`). A
  test's copy of room.git does not trigger them. The mirror's `/api/status` is the last
  committed state, not the live working tree.
- **What does not work on the mirror:** OpenAI (`/api/seer/ask`), because there is no key on the
  box by design; the loopback inlet (`/api/internal/event` → 403 through any proxy); job
  progress from roomctl, which only reaches the laptop's web. The remote-edge job/result
  endpoints are D46.

**What it costs, and what stops it** (none of these depend on anyone watching):

| guard | setting |
|---|---|
| project | `gitspace-htn-2026`, billing `016E9C-6EB886-62508E`; only Compute, Budgets and IAP enabled |
| budget | **CA$10/month**, email alerts at 25 / 50 / 90 / 100 % |
| size | one `e2-small` (~CA$0.03/h), 10 GB `pd-standard`, ephemeral IP (not reserved), no GPU |
| **hard stop** | `--termination-time=2026-09-21T12:00:00Z --instance-termination-action=STOP`: Google stops it; then only the disk bills (~CA$0.50/month) |
| blast radius | no service account on the VM; SSH only through IAP (35.235.240.0/20); RDP rule deleted; 80/443 only on the `web` tag |
| secrets on the box | ES + Sentry + `GITIRL_CLOUD_TOKEN` (the edge's bearer token for `POST /api/jobs/{id}/result`), `.env` 600 owned by `gitspace`; job ledger `JOBS_DIR=/srv/gitspace/jobs` (700); **no OpenAI / GitHub / AWS keys**. `JOBS_REAL_MOTION` is unset, so every job says `motion: mock_only` until the user flips it |
| Vercel | Hobby plan: hard limits, no overage billing |

Operate it: `scripts/gcp_mirror.sh status | sync-room | ship | logs | stop | start` and
`scripts/deploy_vercel.sh`. When the weekend is over: `gcloud projects delete gitspace-htn-2026`
removes everything, disk included.

**The Funnel URL below went down at ~07:25Z** (TLS dropped at Tailscale's ingress on :443 and
:8443, from inside and outside; the node is online, the cert is valid until Dec 16). It started
when venue wifi moved the laptop between networks (10.36.x → 10.37.x). It is kept as a
secondary door and is no longer the URL to give judges (docs/10 D48).

## The URL we actually use now: Tailscale Funnel (2026-09-19)

~~Devpost URL~~ (superseded by the Vercel URL above): `https://daniels-macbook-pro.tailaa0f4f.ts.net:8443` → the web tier on `127.0.0.1:8000`.

Funnel was already enabled on this laptop (tailscale 1.98.8), with `:443` proxying an older
project on `127.0.0.1:3001` (nothing listening there as of 06:5xZ). We added **port 8443** and
left 443 untouched: `tailscale funnel --bg --https=8443 http://127.0.0.1:8000`. Undo:
`tailscale funnel --https=8443 off`.

| | Funnel (now) | named Cloudflare tunnel (`repr.ink`, scripted) |
|---|---|---|
| stable URL | **yes, today**, `*.ts.net` | yes, once the domain is bought |
| cost / steps | none | a domain + Cloudflare setup + one login |
| survives restarts | yes (tailscaled keeps the serve config) | yes (launchd agent) |
| custom domain | **no** — Funnel serves only `*.ts.net` with Tailscale's certs | **yes** |
| serves from | this laptop (a sleeping laptop is down) | this laptop, or AWS later with the same hostname |

**Verdict:** Funnel for the submission now; the named tunnel stays ready for `repr.ink`. Both can
run at once — they're two doors onto the same `:8000`. The port in the URL is the price of not
touching `:443`; if the 3001 project is retired, `tailscale funnel --bg http://127.0.0.1:8000`
takes the bare URL. Renaming the machine in Tailscale (e.g. `gitirl`) would change BOTH URLs.

**Checked through the PUBLIC ingress** (resolved via 1.1.1.1, not the tailnet): `/api/health` 200;
the loopback-only inlet `POST /api/internal/event` → **403** (Funnel adds forwarding headers, so
the internet is not mistaken for localhost); a WebSocket upgrade to `/ws/gitirl-agent` over
HTTP/1.1 → **403** (the same upgrade from localhost → 101). Sentry Uptime #10384065 and
`WEB_PUBLIC_URL` point here.

