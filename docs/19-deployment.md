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
| `web/` dashboard + API + webhooks | **AWS t4g.small** | no — and that's fine, it's not the critical path |
| Elasticsearch | Elastic Serverless, **GCP `us-east4`** | no — and the web tier reaches it cross-cloud, same metro |
| heavy models (SAM 3, VLM) | **Baseten** | no — fall back to local YOLO if it drops |
| point-cloud blobs | S3, cached locally first | no — write locally, upload opportunistically |

**Design rule:** anything the demo cannot survive losing runs on the local network. Everything
else can live in the cloud.
