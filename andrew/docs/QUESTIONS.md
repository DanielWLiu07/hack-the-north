# BracketBot Information Checklist

## PERCEPTION

- [ ] What environment representation is exposed?
- [ ] Are object detections/poses accessible directly?
- [ ] What perception capabilities are built in?

## ACTIONS

- [ ] How are VLA actions invoked programmatically?
- [ ] What manipulation primitives are available?
- [ ] What is the recommended abstraction for pick/place/movement?

## VERIFICATION

- [ ] How can software determine action completion/success/failure?
- [ ] Can we immediately re-observe after an action?

## STATE

- [ ] Does BracketBot maintain any world state/memory internally?

## DAEMONS

- [ ] What existing daemons are relevant?
- [ ] How do our applications communicate with them?
- [ ] Which portions should we extend versus leave untouched?

## RELIABILITY

- [ ] Which object shapes/sizes/materials work most reliably?
- [ ] Known failure modes?
- [ ] Typical latency?
- [ ] Environmental constraints?

## HARDWARE

- [ ] Which sensors/cameras must remain unobstructed?
- [ ] Can lightweight decorations be attached safely?
- [ ] Where can we safely attach a small propeller hat?
