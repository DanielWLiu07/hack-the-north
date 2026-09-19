# Credentials checklist

Copy [`.env.example`](.env.example) → `.env` and fill in. **`.env` is never committed.**

## Get these tonight (2026-09-17)
| key | where | why tonight |
|---|---|---|
| `ELASTIC_URL` + `ELASTIC_API_KEY` | cloud.elastic.co → **Serverless → Elasticsearch** | signup + index creation on venue wifi at hour 20 is a bad time. Confirm **Agents** appears in the nav — if not you picked the wrong deployment type |
| `HF_TOKEN` | huggingface.co/settings/tokens | needed to pre-download model weights, which you must do on a trusted network |
| `PI_HOST` / `LAPTOP_IP` | your own travel router | everything else assumes them |
| `JINA_API_KEY` | jina.ai — **only if EIS doesn't serve jina-embeddings-v3** | check EIS first; you may not need it at all |

## Collect at the booths Friday morning
| key | booth | note |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI | **they hand out credits.** Go early. With credits, running the VLM three times per object is free — and that's what makes hybrid search meaningful |
| `BASETEN_API_KEY` | Baseten | credits; only if offloading SAM 3 / the VLM |
| — | Elastic | ask about a sponsor org, and confirm current `semantic_text` + Jina syntax |
| — | Bracket Bot | the questions in [`docs/10-open-questions.md`](docs/10-open-questions.md) |

## Optional, in build order
1. `TELEGRAM_BOT_TOKEN` — 10 min, unlocks "text your room". No phone number needed.
2. `SENTRY_DSN` — you'll be debugging the pipeline anyway; tracing pays for itself.
3. `ELEVENLABS_API_KEY` — 30 min, materially better demo narration.
4. `AWS_*` — S3 for clouds, SNS for alerts, SQS instead of a tunnel.
5. `GITHUB_*` — only for the pull-request beat.

## Two that aren't secrets but will ruin your day if wrong

**`ROOM_CUBE_SIZE` / `ROOM_ORIGIN_*`** — every octree key in Elasticsearch is relative to this
cube. Change it after ingesting and all history is silently invalidated. Pin it, commit it to
`room.yaml`, never touch it.

**`ANCHOR_TAG_ID`** — the AprilTag that defines the world origin. If the tag moves, every
historical pose becomes wrong with no error. Tape it down.

## Hygiene
- `.env` in `.gitignore` from the very first commit.
- The **laptop holds every cloud credential**. The Pi holds none — it pushes telemetry over the
  WebSocket and the laptop does the writing.
- If you push `room.git` to GitHub, double-check no `.env` and no raw imagery went with it.
