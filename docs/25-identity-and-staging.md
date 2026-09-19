# 25 — What identifies a thing, and what `git add` means

Two questions that turn out to be the same question.

---

## 1. A point cloud contains no objects

It contains points. "Object" is not in the data — it is something we **impose**. Nothing in a
cloud says *"these 1,420 points are a mug"*; the points do not know they belong together.

So identity is a **decision we make fresh on every scan**, and the entire system's correctness
reduces to one thing:

> **Make the same decision twice about the same physical thing.**

That is why [`test_idempotent_scan`](../tests/) is the project's most important test. It is not
testing the cameras. It is testing whether our idea of "object" is stable.

---

## 2. Four weak signals, no strong one

A mug carries no serial number. Identity has to be inferred, and every available signal fails
in a different place:

| signal | says | fails when |
|---|---|---|
| **position** | things stay put | **the object moved** — which is the entire point of the project |
| **appearance** | colour, size, extents | lighting shifts, or a different camera sees a different face |
| **semantics** | "it is a mug" | there are two mugs |
| **continuity** | it was here a second ago | the object left and came back |

Note the cruelty of the first row: our strongest signal is *stability*, and we are building a
system whose only job is to detect **change**. Position alone would report every real move as
"one object vanished, a different one appeared."

None of the four works alone. `associate.py` weighs all four together — and this is why the
hybrid search in Elasticsearch is load-bearing rather than decorative: it is how the
appearance and semantic signals get compared against **history**, not just against the
previous frame.

---

## 3. Identity is assigned, then *defended* — not detected

This is the mental shift that makes the rest obvious.

```
first sight      →  ASSIGN an id.   mug_a1b2 is born here and nowhere else.
every scan after →  DEFEND it.      "which thing I already know is this most likely to be?"
```

We are **never** asking *"what is this?"* after the first time. We are asking *"which of my
existing objects does this cluster correspond to?"* — that is **association**, not
classification, and it is a completely different computation:

```python
# NOT this — a fresh answer every scan, so the id changes when the object does
object_id = classify(cluster)

# THIS — the id is carried across, geometry only decides the MATCH
object_id = best_match(cluster, known_objects) or mint_new_id()
```

### The id itself

```python
object_id = f"{class_slug}_{sha1(f'{class_slug}|{first_seen_commit}|{ordinal}')[:4]}"
```

Assigned **once**, at first sight, then carried forward by matching. Note what is *not* in it:
**no position, no extents, no colour.** An id derived from geometry changes when the object
moves — which renames the file — which turns every move into a delete plus an add.

### Why that failure is so bad

The filename **is** the identity: `zones/desk/mug_a1b2.yaml`. So a broken association does not
produce a slightly wrong diff, it produces a **structurally** wrong one:

```diff
  # what actually happened: the mug moved 19 cm
- zones/desk/mug_a1b2.yaml        ← deleted
+ zones/desk/mug_7f3e.yaml        ← added
```

`git log --follow` breaks. `git blame` on the object loses its history. The robot is told to
*remove* a mug that is still there and *fetch* one that does not exist — and it will report
`cannot apply hunk`, correctly, for a move it should simply have performed.

**One bad association costs you the object's entire past.**

---

## 4. So how do the points become a thing at all?

Two paths, and [`15-segmentation.md`](15-segmentation.md) has the detail. In short:

```
PRIMARY   the segmentation model decides, in 2D, where object boundaries are.
          xyz is aligned pixel-for-pixel with the rectified left image, so:
              pts = xyz[mask & valid]        ← that is the whole lift to 3D
          A model understands OBJECTS. It separates a mug touching a book.

FALLBACK  geometry decides: remove the support planes, then cluster what is left
          by point spacing. Understands SPACING, not objects — so it merges
          anything touching. Used for things the model does not recognise,
          which become class: unknown but are still tracked.
```

Then `merge.py` unifies the three cameras' opinions of the same physical thing, and
`associate.py` matches that against history.

**The model proposes; association disposes.** Segmentation decides *what is a thing in this
frame*. Only association decides *which thing it is*.

---

## 5. What `git add` means when the working tree is a room

In git, `add` moves changes from the working tree into the index. Here the working tree is the
room as observed, so:

> **`room add` is selective attention.** You are saying: *these* changes are the ones I mean to
> record; leave the rest untracked for now.

Because a **zone is a directory** and **one object is one file**, git's own path granularity
does all the work:

| command | means |
|---|---|
| `room add zones/desk/` | record what changed on the desk; ignore the shelf and the floor |
| `room add zones/desk/mug_a1b2.yaml` | record just this one object's move |
| `room add -A` | record everything, including things you did not do on purpose |
| `room commit` | freeze the staged set as the new truth |

The realistic case: **you deliberately tidy the desk while someone's bag is sitting on the
floor.** `room add zones/desk/` commits the tidying and leaves the bag untracked — so
tomorrow's `room status` still reports the bag, because nobody has claimed it was meant to be
there.

### The subtle part, which is also correct

Staging captures the **observation at `add` time**, not a promise about the future. If the room
changes between `room add` and `room commit`, what gets committed is what was seen when you
staged. That is exactly how git behaves with file content, and here it gives you something
useful for free:

> **`room add` freezes a moment.** Stage the desk, keep working, commit later — the desk that
> gets recorded is the desk as it was when you said so.

---

## 6. What is deliberately NOT an object

Not everything in the cloud is a thing to track:

| in the cloud | treated as | why |
|---|---|---|
| floor, walls, table top | **zones** (support planes) | they are the coordinate system, not contents |
| the robot's own arm | filtered by kinematics | it is in every frame by construction |
| people | **never committed** | privacy, and they move constantly. `.roomignore` ships with `person` |
| chairs, cables, bags | `.roomignore`-able | they move constantly and would keep `status` permanently dirty |
| unrecognised clusters | `class: unknown`, still tracked | geometry is trackable even when semantics fail |

`.roomignore` is what keeps the working tree from being permanently dirty for reasons nobody
cares about — the same job `.gitignore` does for build artifacts.

---

## 7. The short version

- A point cloud has no objects; we impose them, and must impose them **identically twice**.
- No single signal identifies a physical thing. Four weak ones get combined.
- Identity is **assigned once and defended after** — association, never re-classification.
- The id contains nothing that can change, because **the filename is the identity**.
- Get association wrong and a move becomes a delete-plus-add, which costs the object its
  entire history and makes the robot do the wrong thing.
- `git add` is **selective attention** over a room, and zones-as-directories make it work with
  no new machinery.
