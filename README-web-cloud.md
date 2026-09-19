# Hack the North: web and cloud

This repository contains the GITIRL browser experience, cloud data services,
and telemetry monitoring. Andrew's ML agent and robot/cloud translation layer
are maintained in his separate repository.

## Contents

- `web/`: FastAPI server, dashboard, capture and object pages, telemetry UI,
  and the animated landing page.
- `elastic/`: Elasticsearch mappings, queries, ingestion, and tests.
- `telemetry/`: telemetry hub, frame cache, and integration tests.
- `obs.py`: shared observability setup.
- `scripts/`: existing development and observability utilities.

## Integration boundaries

This is a scoped source repository, not a standalone robot deployment.
Some ingestion and room operations import the local `roomctl` and
`perception` packages, which are not included here. Some development scripts
also assume the full integration workspace. Connect those dependencies before
running those workflows. The interface documentation is in `docs/`.

Keep credentials in an untracked `.env` or deployment environment. Never commit
API keys, local recordings, virtual environments, or the runtime `room.git` data.
See `web/requirements.txt` and `elastic/requirements.txt` for service dependencies.

The initial commits group the existing source snapshot by component. They do not
represent its historical development sequence; commit timestamps are unmodified.
