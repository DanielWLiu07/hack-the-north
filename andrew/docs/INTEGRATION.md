# Caretaker integration

## Edge → Daniel cloud

The cloud link is outbound-only HTTPS. Do not set `HOUSEBOT_EDGE_URL`, expose
port `8780`, or add a tunnel for this path. Configure:

```bash
GITIRL_CLOUD_BASE_URL=https://daniels-macbook-pro.tailaa0f4f.ts.net
GITIRL_CLOUD_TOKEN=<Daniel provides this privately>
GITIRL_CLOUD_TIMEOUT_SECONDS=30
```

`CloudAPIClient` in `transport/cloud.py` supports state, semantic search,
explicit command planning, job reads, and authenticated terminal reports. The
token is sent only by `report()`; reads and command planning follow Daniel's
current unauthenticated contract. Missing report credentials fail locally
before a request is made.

Job discovery is not implemented. A read-only live probe of `GET /api/events`
returned only a `status` event, with no complete job, job ID, claim, or replay
contract. Calling `discover_jobs()` therefore raises
`CloudJobDiscoveryUnavailable`. Daniel must either define job delivery over
that outbound stream or add a read/claim queue; the edge must not poll guessed
IDs or enable the cloud-to-edge dispatcher as a workaround.

Live payloads identify their frame as `world_z_up`. The edge preserves that
label; it does not assume those coordinates are already in BracketBot's
`slam_world` frame. A measured, versioned cloud-to-SLAM transform is required
before any cloud pose can become a navigation target.

Safe smoke checks are documented in `CLOUD_CONTRACT.md`. The temporary
Cloudflare hostname recorded there did not resolve on 2026-09-19; use the
primary Tailscale HTTPS name unless Daniel publishes a new fallback.

## Edge → robot

The edge calls:

- `GET /health`
- `GET /v1/observation?request_id=...`
- `POST /v1/actions`

Still needed from Ryan/Sarah:

- a real `RobotAPIBackend` implementation;
- support for one finite `POINT_AT_OBJECT` or `MOVE_OBJECT` action;
- world-to-robot coordinate conversion;
- terminal success/retryable/failure semantics;
- writer-busy detection, timeout, cancellation, and torque release;
- a fresh observation if physical verification is used.

Sarah's current named joint-pose CLI is not yet this service contract and must not be invoked concurrently with another BBOS writer.

## First demo path

```text
“Where are my keys?”
→ Daniel /api/search
→ keys_7c2e
→ Daniel /point job
→ POST complete job to Housebot Edge
→ POINT_AT_OBJECT
→ robot points
→ terminal result returned
```

Do this five times before adding manipulation, Git history, or NLP polish.
