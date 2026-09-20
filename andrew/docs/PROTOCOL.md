# Local development protocol

These envelopes are legacy CLI/JSONL development fixtures. They are not the caretaker HTTP contract.

> **Daniel's backend schema is authoritative. Adapt `protocol/daniel.py` rather than spreading backend-specific assumptions throughout the edge.**

## Envelope

Every message uses this provisional shape:

```json
{
  "type": "user_command",
  "request_id": "abc123",
  "timestamp": "2026-09-18T12:00:00+00:00",
  "payload": {}
}
```

`timestamp` is optional on input and emitted by the development service. Payload shape depends on `type`.

## Incoming events

### `user_command`

```json
{
  "type": "user_command",
  "request_id": "abc123",
  "payload": {"text": "set my room back to study mode"}
}
```

### `robot_status`

Provisional payload: `status`, optional `message`, and optional `metadata`.

### `robot_observation`

Provisional payload: an `observation` containing `objects` and optional state metadata. Position and orientation values are deliberately opaque.

### `robot_action_result`

Provisional payload: `result` with `status`, optional `message`, and optional `observations`.

The three robot-originated events are modeled for fixtures but are not a network transport. Real robot calls use the `RobotAdapter` HTTP boundary.

## Outgoing events

### `parsed_command`

```json
{
  "type": "parsed_command",
  "request_id": "abc123",
  "payload": {
    "command": "restore",
    "target_state": "study"
  }
}
```

### `robot_action`

Carries a high-level action such as `MOVE_OBJECT`. The wire-level routing and acknowledgement behavior are TBD.

### `command_status`

Carries an intermediate status and optional human-readable message.

### `command_result`

Carries terminal `status`, `message`, `attempts`, and optionally structured `differences`. A diff entry includes `object_id`, `change_type`, `current_state`, and `desired_state` so Daniel can choose how to render it.

Example diff result:

```json
{
  "type": "command_result",
  "request_id": "abc123",
  "payload": {
    "status": "DIFF_COMPLETE",
    "message": "Found 1 changed object(s)",
    "attempts": 0,
    "differences": [
      {
        "object_id": "box_A",
        "change_type": "MOVED",
        "current_state": {"object_id": "box_A", "position": "Y"},
        "desired_state": {"object_id": "box_A", "position": "X"}
      }
    ]
  }
}
```

### `error`

```json
{
  "type": "error",
  "request_id": "abc123",
  "payload": {
    "code": "unknown_command",
    "message": "Unknown or ambiguous command",
    "details": {}
  }
}
```

## `request_id`

The sender creates a non-empty request ID. All parsed commands, actions, statuses, results, and errors caused by that request preserve it. The final authority for request-ID generation and uniqueness is Daniel's backend.

## Idempotency expectations

- Receiving the same `request_id` more than once must not silently trigger duplicate physical actions.
- The central backend and this service must agree which side records completed/in-flight IDs.
- Idempotency storage is not implemented until persistence ownership and delivery semantics are finalized.

## Versioning

No cloud protocol version is claimed here. Daniel's current HTTP shapes are translated in one module and should gain an explicit version when his backend contract stabilizes.
