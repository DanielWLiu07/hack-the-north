# 32 — State and durability: what lives where, and what dies with the laptop

Inventory taken 2026-09-19 ~07:05Z by the cloud session. "Durable" = survives this laptop's disk.

| state | where it lives | durable? | if lost | status |
|---|---|---|---|---|
| **`room.git`** — the room's history, the source of truth | laptop disk **+ GitHub `DanielWLiu07/room` (private)**, pushed every 60 s by `scripts/room_backup.sh` (launchd) | **yes, since 07:06Z** | — | **done**: 3 branches + tag `study` mirrored |
| **the code** — six sessions' work | laptop disk; `origin` = `hack-the-north.git` has 4 commits, the last ~2 h old; **155 uncommitted paths** | **NO** | tonight's work | **a human**: commit + push (this session was told not to commit) |
| `room-objects`, `room-voxels`, `room-clouds`, `room-events` | Elastic Serverless (managed) | yes, kept | derived from room.git + captures: rebuildable | fine |
| `room-observations` (raw per-camera sightings) | Elastic TSDS, **no** retention set | yes, kept | NOT rebuildable — but it is managed and kept | fine |
| `robot-telemetry` | Elastic TSDS | **7-day retention**, downsampled 1 h → 5 min, 6 h → 30 min | raw 50 Hz exists for one hour | by design |
| telemetry spool | `~/.cache/gitspace/telemetry-spool` | transient | the unsent seconds, only if the laptop dies mid-outage | fine |
| point clouds (`.ply`) | `./clouds` (empty); **the S3 bucket was never created** | **NO** (nothing stored yet) | `room-clouds.cloud_uri` would point at nothing | blocked on AWS credentials |
| failure photos | `~/.cache/gitspace/frames` (newest 50) → one per failure in Sentry | Sentry keeps the attached one | fine | fine |
| Sentry issues / traces / logs / replays | sentry.io | per-plan retention | — | trial ends 2026-10-02: move to the education plan |
| the panel's staging (htn:5, `perception/devgraph.py`) | writes only room.git | via room.git's backup | — | fine |
| bridge idempotency cache, web's SSE ring | process memory | no — volatile by design | after a restart a retried `request_id` can PLAN again (never move) | acceptable |
| attachment-budget ledger | `~/.cache/gitspace/attach-ledger.json` | local, advisory | the hour's budget resets | fine |
| `.env` secrets | laptop only, never committed | by design | re-issue from each provider | keep a password-manager copy |
| model weights | `~/.cache/gitspace/models` | re-downloadable (`bootstrap_laptop.sh`) | a re-download | fine |
| Rerun recordings (`.rrd`) | nowhere — `snapshot.sh` isn't built (D16) | **NO** | the demo's recordings | open |

## room.git's remote

- **GitHub, private: `DanielWLiu07/room`** — the name `.env`'s `ROOM_REPO` already expected. It holds
  only YAML object records, `room.yaml`, an anchor and `.roomignore`: **no imagery** (checked before
  the first push; KEYS.md's rule).
- **Pushed continuously, from every writer** (the panel's backend, `room` CLI, the fake scene
  generator) by `scripts/room_backup.sh`, run by launchd every 60 s. No hook to install or collide
  with. Offline minutes just retry.
- **It never loses history.** Branches deleted locally stay on the remote; a branch rewritten
  locally (`room reset --hard`) moves on the remote too, but the remote's old tip is first kept as
  `backup/<branch>/<sha7>`; tags are never forced. Tested on throwaway repos (`scripts/test_room_backup.py`).
- **Next, for R1:** a second remote on the Pi (a bare repo over the LAN) keeps a copy even with no
  internet. Same script, one more `git remote add`, once the Pi is up.

## Do we need a third database? No.

Git is the write path and the truth, and it now has an off-site copy. Elasticsearch is the read
path, managed, and it holds everything we can't rebuild (observations, telemetry). A third store
(Postgres, Redis, SQLite…) would add a second source of truth for the same facts — the one design
mistake docs/04 warns about — and a new thing to back up. The two real gaps are **blobs** (point
clouds → S3, planned, blocked on AWS credentials) and **the code** (commit and push). Neither is a
database.
