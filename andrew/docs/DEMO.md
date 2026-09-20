# Caretaker demo

## Live demo

1. Ask: “Where are my keys?”
2. Daniel's UI shows the matched object and why it matched.
3. The edge receives the complete point job.
4. The robot points at the last-known location.
5. UI reports the terminal result.

Optional upgrade: ask the robot to put one curated object back.

## Backup demo

Use the same cloud and edge path with the mock robot adapter, clearly labelled as a mock.

## Failure recovery

- Unknown/ambiguous object: ask for clarification.
- Robot busy: do not start another action.
- Action failure: report it; do not blindly replay.
- Manipulation unreliable: keep the point-only demo.
