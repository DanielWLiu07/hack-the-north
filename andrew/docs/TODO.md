# MVP TODO

## Before robot integration

- [ ] Exchange one real Daniel `point` job fixture.
- [ ] Run Daniel → edge mock successfully.
- [ ] Agree on synchronous result versus Daniel callback endpoint.
- [ ] Confirm pose frame, units, confidence gate, and `job_id` rules.

## Robot integration

- [ ] Ryan/Sarah implement one finite `POINT_AT_OBJECT` action.
- [ ] Confirm world-to-robot transform and writer ownership.
- [ ] Return structured terminal success/failure.
- [ ] Run the real point demo five times.

## Only after P0

- [ ] Add one curated `MOVE_OBJECT` action.
- [ ] Add fresh-observation verification.
- [ ] Add durable job idempotency/restart recovery.
- [ ] Decide final product and repository name.
