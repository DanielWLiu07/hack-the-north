# 17 — Text messaging and AWS

## Start with the honest framing

**There is no AWS prize at Hack the North 2026.** I checked the full sponsor list — Bracket
Bot, OpenAI, RBC, Shopify, Warp, GPTZero, QNX, Dryft, Elastic, Baseten, Rox, CSE, Tether,
Intact, Dominion Dynamics, Expo, Sentry, Aramco, Human Computer Lab, Backboard, Huawei, and
the MLH set. No AWS.

So AWS earns its place here **only where it solves a real problem**, not for points. Three
services do. Everything else would be padding, and padding dilutes the pitch.

| service | what it solves | verdict |
|---|---|---|
| **S3** | the point-cloud blobs currently sitting on a laptop's local disk | **yes** — makes the blob/catalog split real |
| **SNS** | outbound SMS | **yes** — cheap, and it enables the best remote beat |
| **SQS** | inbound webhooks **without** poking a hole in venue wifi | **yes** — genuinely better than a tunnel |
| IoT Core / MQTT | replacing the Pi↔laptop WebSocket | **no** — see §4 |
| Bedrock | LLM | no — conflicts with the OpenAI story |
| Kinesis | telemetry stream | no — Elasticsearch is the telemetry store |
| CloudWatch | observability | no — Sentry covers it |

---

## 1. Text messaging: you text your room

This is a genuinely good demo beat and it costs very little.

### The beats

**A — Remote status.** Text `status` to the room's number:
> **You:** status
> **Room:** 🟡 working tree dirty · 3 changes on `main`
> `~ mug_a1b2 moved 0.19 m` · `- marker_c3d4 removed` · `+ scissors_9f3a untracked`

**B — Remote query, physical answer.** This is the one to show:
> **You:** where did I leave my hammer
> **Room:** last seen `zones/desk`, 14:22, commit `a3f9c1` — 2 commits ago. Driving over to
> point at it now. 🤖

You are standing in a different building and a robot walks to a spot and points. That lands.

**C — Merge conflict resolution, human in the loop.** The robot hits a conflict, stops, and
**texts you**:
> **Room:** ⛔ CONFLICT — `mug_a1b2` moved in both `main` and `movie-night`. Reply OURS or THEIRS.
> **You:** theirs
> **Room:** Applying `movie-night`. Moving mug to (0.61, 0.18, 0.75).

A physical merge conflict escalating to a human over SMS, resolved by a one-word reply, and
then executed by an arm. That is the most memorable 20 seconds available to us.

**D — Alerts.** Failed grasp, robot fell over, ingest broken. At 4am this is how you find out
without staring at a terminal.

### Outbound: AWS SNS — easy, do it tonight
```python
import boto3
sns = boto3.client("sns", region_name="us-east-1")
sns.publish(PhoneNumber="+1519...", Message="⛔ CONFLICT — mug_a1b2. Reply OURS or THEIRS.")
```
**The one catch:** a new account is in the **SMS sandbox**, where you can only send to
*verified* destination numbers. Verifying your four team numbers takes about two minutes each
in the console, and that is all a demo needs. Do it tonight, not Saturday.

### Inbound: the honest problem
Receiving SMS needs a **provisioned phone number**, and on AWS a US number requires 10DLC
registration, which takes **days to weeks**. That does not fit in a hackathon.

Three ways out, ranked:

| option | inbound works? | setup | note |
|---|---|---|---|
| **Telegram bot** | **yes, instantly** | ~10 min, no number, no registration | **recommended.** BotFather → token → long-poll. Also gives you images, so you can send back the Rerun screenshot |
| Twilio trial number | yes | ~15 min | a real phone number, instantly; trial numbers can receive |
| AWS inbound SMS | eventually | days | do not attempt this weekend |

**Recommendation: Telegram for two-way, SNS for outbound SMS alerts.** You get the "text your
room" beat with no provisioning risk, *and* you can send the diff as an image, which SMS
can't do. If someone insists it must be a real phone number for the demo, use a Twilio trial
number and keep SNS for alerts.

### Where it lives
`agent/messaging.py` on the laptop. An inbound message is just **another way to call an agent
tool** — exactly the same tool surface the CLI and the voice interface use. Do not build a
parallel command parser:

```
SMS / Telegram ─┐
voice (Realtime)─┼──► agent/loop.py ──► tools (retrieve: ES · act: robot) ──► THE ROOM
CLI (roomctl) ──┘
```
Three front ends, one brain. That's also why adding a fourth later costs nothing.

---

## 2. S3 for the cloud blobs

[`11-elastic.md`](11-elastic.md) already specifies the blob/catalog split: the `.ply` sits on
disk, Elasticsearch stores a document *about* it. S3 makes that real.

```python
s3.upload_file(f"/tmp/{capture_id}.ply", BUCKET, f"clouds/{capture_id}.ply")
es.index(index="room-clouds", id=capture_id, document={
    "capture_id": capture_id, "@timestamp": ts, "commit_sha": sha,
    "cloud_uri": f"s3://{BUCKET}/clouds/{capture_id}.ply",
    "point_count": 2_841_003, "coverage_pct": 0.83, "icp_residual_mm": 4.2,
    "cameras": ["cam0","cam1","cam2"] })
```

Why it's worth the twenty minutes:
- **The laptop stops being a single point of failure** for the one artifact you can't regenerate.
- A teammate can pull any historical cloud without your machine being awake.
- `cloud_uri` becomes a real URI instead of a path that only works on one laptop.
- Set a **lifecycle rule to expire after 7 days** so you don't leave a bucket of point clouds
  costing money after the hackathon.

Use a presigned URL if you want the Rerun ghost render to load a historical cloud from a
different machine.

---

## 3. SQS for inbound webhooks — the non-obvious win

[`16-api.md` §5](16-api.md) says the GitHub and Agent Builder webhooks need a public URL via
`ngrok`/`cloudflared`. That works until venue wifi blocks it, your tunnel URL rotates, or
captive-portal NAT does something creative. All three are likely at a hackathon.

**The fix removes inbound connectivity entirely:**

```
GitHub PR merged ──► API Gateway ──► Lambda ──► SQS queue
                                                    │
                                    laptop LONG-POLLS outbound ──► roomctl executes
```

The laptop makes an **outbound** connection to poll SQS. No tunnel, no public URL, no inbound
firewall hole, survives an IP change, and messages **queue up** if the laptop is rebooting
rather than being lost. That is a genuinely better design than a tunnel, not an AWS excuse.

```python
while True:
    r = sqs.receive_message(QueueUrl=Q, WaitTimeSeconds=20, MaxNumberOfMessages=5)
    for m in r.get("Messages", []):
        handle(json.loads(m["Body"]))            # allow-listed tool names ONLY
        sqs.delete_message(QueueUrl=Q, ReceiptHandle=m["ReceiptHandle"])
```

Same pattern for the Elastic Agent Builder workflow callback. The Lambda is ~15 lines: verify
the signature, drop the payload on the queue, return 200.

**Security:** validate every inbound command against an **allow-list of tool names**. Never
dispatch a tool name straight off the wire, and verify GitHub's `X-Hub-Signature-256` in the
Lambda before it reaches the queue.

---

## 4. What NOT to move to AWS

**Do not replace the Pi↔laptop WebSocket with IoT Core / MQTT.** The link between the robot
and the laptop **must work with no internet at all** — that is the whole reason for the travel
router in [`08-risks.md` R1](08-risks.md). Routing 400 telemetry messages a second through a
cloud broker adds latency, a hard internet dependency, and a new failure mode, to solve a
problem we do not have. Local stays local.

Same logic for Kinesis (Elasticsearch is the telemetry store), Bedrock (conflicts with the
OpenAI narrative), and CloudWatch (Sentry).

---

## 5. Total cost and time

| item | setup | runtime cost |
|---|---|---|
| SNS outbound SMS + sandbox verification | 20 min | fractions of a cent per message |
| Telegram bot (two-way) | 10 min | free |
| S3 bucket + lifecycle rule | 20 min | pennies for a weekend |
| API Gateway + Lambda + SQS | 45 min | free tier |
| **total** | **~1.5 h** | **~$1** |

**Build order:** Telegram first (10 minutes, unlocks beats A–C), SNS alerts second (they save
you time at 4am), S3 third, SQS only if you actually do the GitHub PR beat.

And the standing rule from [`07-prizes.md`](07-prizes.md) still applies: none of this happens
before the core `status` → `diff` → `revert` loop is solid. It's an hour and a half of polish
on a foundation, not a substitute for one.
