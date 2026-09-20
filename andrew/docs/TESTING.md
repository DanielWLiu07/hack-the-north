# Testing

## Automated service tests

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The tests cover raw and natural-language deterministic parsing, structured validation errors, JSON message conversion, JSON state persistence, commit, displayable diffs, high-level planning, successful restore, retry success, retry exhaustion, and unknown saved states.

The mock adapter is test infrastructure only. It does not simulate real perception, timing, collisions, or robot behavior.

## Adapter acceptance tests to add

- A real observation can be translated into `WorldState`.
- Repeated stationary observations compare as unchanged within confirmed tolerances.
- Every supported action maps to an official robot operation.
- Completion and failure signals map correctly to `ActionResult`.
- Re-observation occurs after execution and is fresh.
- Retryable and hard failures are distinguishable.

## Live hackathon matrix

| Test | Expected | Actual | Pass/Fail | Attempts | Notes |
| --- | --- | --- | --- | --- | --- |
| Identify one object | One target object is identified correctly |  |  |  |  |
| Repeat observation consistently | Controlled setup yields consistent observations |  |  |  |  |
| Detect one moved object | Changed object is detected |  |  |  |  |
| Produce correct diff | Diff matches the controlled change |  |  |  |  |
| Restore one object | Object returns to saved state |  |  |  |  |
| Verify one restored object | Re-observation confirms restoration |  |  |  |  |
| Full demo repeated 5x | Complete demo succeeds five times |  |  |  |  |

## Known failure modes

Record the observed setup, signal, mitigation, and whether the issue belongs to the backend, agent, perception, or robot adapter.
