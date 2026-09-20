# Prototype checklist

## 0. Three-process mock

- [ ] Daniel sends a complete point job.
- [ ] Edge validates/translates it.
- [ ] Mock robot returns success.

## 1. Real observation

- [ ] One stable object ID and canonical pose arrive from Daniel.

## 2. Real point action

- [ ] Ryan/Sarah accept one `POINT_AT_OBJECT` request.
- [ ] Robot finishes and releases control writers.
- [ ] Structured result returns to the edge.

## 3. Reliable caretaker demo

- [ ] “Where are my keys?” succeeds five times.
- [ ] Unknown object fails safely.
- [ ] Duplicate job does not execute twice.

## 4. One-object manipulation

- [ ] One curated move succeeds.
- [ ] Re-observation verifies the result.

## Stop gate

Do not add features until the point demo works five times in a row.
