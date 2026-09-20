"""The watch loop: continuous `git status` for a physical room (plan/roommate/03 §6, 01 §1).

Each tick asks one question, "which parts of the room has the robot looked at again?", and re-scans only
when the answer is "some". A change is believed only after it has been seen on TWO separate fresh passes,
because one pass is a glance: a hand on the table, a cup in mid-air, a block the camera only grazed.

    pass        for one 0.5 m block: the robot saw it (age <= fresh_s) AND either it had gone stale since
                the last pass, or that pass was at least pass_gap_s ago (a robot standing still, staring at
                a desk, is not re-observing it eight times a second)
    pending     a change seen on fewer than debounce_passes passes of ITS block
    confirmed   seen on that many. If the change itself changes (moved again), the count starts over.
    clean       nothing confirmed that policy says is ours to act on. Pending is not dirty: that is the
                point of the debounce, and what the room's CI badge (telemetry.room_clean) reports.
    stale ≠ gone  is not decided here: perception.bb_source.scan_into_bb only counts a miss when the block
                is fresh AND the spot is in line of sight, and carries the object forward otherwise.

Without an area map (no rectangle yet, or a stereo-only robot) the whole room is one block. Without a
registration, or while SLAM is not ready, or after a map reset the registration has not caught up with,
nothing is scanned and nothing is concluded: the last verdict stands and `blocked` says why. A scan that RAISES (a
full disk, a half-written file, a bug downstream) is the same kind of event, not a reason to stop watching the room.

Outputs, all optional and all injected: heartbeat(RoomState) every tick; publish("room_state", dict) when the
verdict changes (and every republish_s otherwise, so a live-but-quiet loop is not mistaken for a dead one); jobs(action, change) -> job id for what the robot can do itself (Tier A); chores for
what it cannot. A change the robot has PROVED it cannot fix (the object is not there; there is nowhere to put it)
becomes a chore rather than a job retried for ever: the claim is that it acts on what it can prove and asks when it
cannot, and a red badge with no route out is not asking. `last_verified_job` is the last job (or chore) that a clean
fresh pass came after: the only honest meaning of "verified by rescan".
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from roomctl import chores, policy
from roomctl.repo import Entry, Repo, load_room
from roomctl.state import SchemaError, from_yaml

log = logging.getLogger("roomctl.watch")
WHOLE_ROOM = (-1, -1)        # the one block of a room with no area map


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RoomState:
    clean: bool
    head: str | None
    branch: str
    confirmed: list[dict] = field(default_factory=list)   # {object_id, zone, type, verdict, action, passes, block_age_s, ...}
    pending: list[dict] = field(default_factory=list)     # seen, but not yet on debounce_passes fresh passes
    stale_blocks: int = 0
    at: str = ""
    last_verified_job: str | None = None
    ignored: int = 0                 # changes policy says are none of our business (a personal zone)
    passes: int = 0                  # fresh passes scanned so far
    carried: int = 0                 # objects a running job is holding: the map cannot see them, so they say nothing
    blocked: str | None = None       # why this tick concluded nothing: no_registration | slam_not_ready | map_reset

    def to_dict(self) -> dict:
        return asdict(self)

    def verdict(self) -> tuple:
        """What a subscriber cares about: changes when the room's state does, not when the clock does."""
        rows = lambda xs: tuple(sorted((c["object_id"], c["type"], c["action"]) for c in xs))  # noqa: E731
        return (self.clean, self.head, self.branch, rows(self.confirmed), rows(self.pending),   # not the pass counts:
                self.last_verified_job, self.blocked)                                          # a mess seen a 3rd time is not news


def change_type(e: Entry) -> str:
    if e.untracked:
        return "untracked"
    if e.conflict:
        return "conflict"
    code = e.staged if e.staged != " " else e.unstaged
    return {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(code, "modified")


def _default_scan(repo_dir, nav, reg):
    from perception import bb_source
    return bb_source.scan_into_bb(repo_dir, nav, reg)


class Watch:
    def __init__(self, repo: Repo, nav: Any, reg_provider: Callable[[], tuple | None], *, tier: str = "A",
                 debounce_passes: int = 2, fresh_s: float = 10.0, pass_gap_s: float | None = None,
                 publish: Callable[[str, dict], Any] | None = None, heartbeat: Callable[[RoomState], Any] | None = None,
                 jobs: Callable[[str, dict], str | None] | None = None, act: bool = True,
                 scan: Callable[..., Any] | None = None, clock: Callable[[], float] = time.time,
                 retry_failed_s: float = 120.0, republish_s: float = 30.0):
        tier = str(tier).upper()
        if tier not in policy.TIERS:
            raise ValueError(f"tier must be one of {', '.join(policy.TIERS)}")
        if debounce_passes < 1:
            raise ValueError("debounce_passes must be at least 1")
        self.repo, self.nav, self.reg_provider, self.tier = repo, nav, reg_provider, tier
        self.debounce, self.fresh_s = int(debounce_passes), float(fresh_s)
        self.pass_gap_s = self.fresh_s / 2 if pass_gap_s is None else float(pass_gap_s)
        self.publish, self.heartbeat, self.jobs, self.act = publish, heartbeat, jobs, act
        self.scan, self.clock, self.retry_failed_s = scan or _default_scan, clock, float(retry_failed_s)
        self.republish_s = float(republish_s)
        self._last_pass: dict[tuple[int, int], float] = {}     # block -> when it last counted as a pass
        self._was_fresh: set[tuple[int, int]] = set()
        self._seen: dict[str, dict] = {}                       # object_id -> {sig, passes}
        self._acted: dict[str, dict] = {}                      # object_id -> {sig we acted on, the chore/job ids}
        self._awaiting: list[str] = []                         # jobs/chores waiting for a clean fresh pass
        self._job_objects: dict[str, set] = {}                 # job id -> what it set out to put right
        self._map_gen: int | None = None
        self._passes = 0
        self._published: tuple | None = None
        self._published_at = -1e18
        self._scan_fail: str | None = None
        self.state = RoomState(clean=True, head=None, branch="", at=_iso(clock()), blocked="not_started")
        if hasattr(nav, "on_map_reset"):
            nav.on_map_reset(lambda why=None: self.forget(f"map_reset: {why}" if why else "map_reset"))

    # ── what other parts of the system tell the loop ────────────────────────────────────
    def note_job(self, job_id: str) -> None:
        """A job that changes the room has started (a tidy, an approved PR). It is `verified` by the first
        clean fresh pass after this, and by nothing else."""
        if job_id and job_id not in self._awaiting:
            self._awaiting.append(job_id)

    def forget(self, why: str = "reset") -> None:
        """The map the passes were counted on is gone: every count starts again."""
        self._last_pass.clear(); self._was_fresh.clear(); self._seen.clear()

    # ── one tick ────────────────────────────────────────────────────────────────────────
    def tick(self) -> RoomState:
        now = self.clock()
        blocked, reg = self._registration()
        if blocked:
            self.state = self._restate(now, blocked=blocked)
            return self._emit()
        new = self._new_passes(reg, now)
        if not new:
            self.state = self._restate(now, blocked=None)
            return self._emit()
        try:
            self.scan(self.repo.path, self.nav, reg)
        except Exception as e:  # noqa: BLE001
            # a scan that cannot finish is not a pass. The disk filling up once killed this loop mid-write and left
            # the room frozen with stale blocks and a green badge: the worst shape for it. Stay up, keep the last
            # verdict, say why, and let the heartbeat go on telling the truth.
            why = f"scan_failed: {type(e).__name__}"
            if self._scan_fail != why:
                self._scan_fail = why
                log.warning("watch: a pass could not be completed (%s: %s). The last verdict stands.", type(e).__name__, e)
            self.state = self._restate(now, blocked=why)
            return self._emit()
        self._scan_fail = None
        self._passes += 1
        st = self.repo.status()
        room = load_room(self.repo.path)
        decided = policy.decided_objects(self.repo)
        confirmed, pending, ignored, carried, live = [], [], 0, 0, set()
        head_recs = None
        by_object: dict[str, list[Entry]] = {}
        for e in st.entries:                                     # a move across zones is two entries (deleted there,
            if e.object_id is not None:                          # untracked here) and ONE change
                by_object.setdefault(e.object_id, []).append(e)
        flight = self._in_flight()
        for oid, entries in by_object.items():
            if oid in flight:
                carried += 1                                     # in the robot's own hand: not evidence of anything
                continue
            entries.sort(key=lambda e: (e.untracked, e.path))    # the tracked side decides what it is
            e = entries[0]
            verdict, action = policy.classify(e, room, st.head, self.tier, decided)
            if action == "ignore":
                ignored += 1
                continue
            if head_recs is None:
                head_recs = self.repo.records() if st.head else {}
            blocks = {self._block_of(x, head_recs, reg) for x in entries}
            sig = "|".join(self._signature(x) for x in entries)
            seen = self._seen.get(oid)
            if seen is None or seen["sig"] != sig:                # a different change: the count starts over
                seen = self._seen[oid] = {"sig": sig, "passes": 0}
            if blocks & new or None in blocks:
                seen["passes"] += 1
            live.add(oid)
            block = next((b for b in blocks if b in new), next(iter(blocks)))
            moved = len(entries) > 1 and any(x.untracked for x in entries) and not e.untracked
            row = {"object_id": oid, "zone": e.zone, "type": "moved" if moved else change_type(e), "verdict": verdict,
                   "action": action, "passes": seen["passes"], "block_age_s": self._age(block),
                   "owner": policy.zone_owner(room, e.zone), "path": e.path,
                   **({"to_zone": entries[-1].zone} if moved else {}), **self._acted.get(oid, {}).get("ids", {})}
            (confirmed if seen["passes"] >= self.debounce else pending).append(row)
        for oid in [o for o in self._seen if o not in live]:    # it went back: the debounce forgets it
            del self._seen[oid]
        clean = not confirmed
        self._close_fixed(confirmed, pending, now)
        last = self.state.last_verified_job
        # Verified = a clean fresh pass after the job ENDED, with nothing still unsettled about WHAT IT TOUCHED.
        # Requiring nothing pending at all was too strong: one flicker elsewhere (a phantom that lasts a pass)
        # would block the proof for ever, and the room would sit correct but never confirmed.
        unsettled = {r["object_id"] for r in pending}
        ended = [j for j in self._awaiting
                 if not self._job_running(j) and not (self._job_objects.get(j, set()) & unsettled)]
        if clean and ended:
            last, self._awaiting = ended[-1], [j for j in self._awaiting if j not in ended]
        self.state = RoomState(clean=clean, head=st.head[:7] if st.head else None, branch=st.branch,
                               confirmed=confirmed, pending=pending, stale_blocks=self._stale_count(),
                               at=_iso(now), last_verified_job=last, ignored=ignored, passes=self._passes,
                               carried=carried)
        if self.act:
            self._act(confirmed, now)
        return self._emit()

    # ── registration and passes ─────────────────────────────────────────────────────────
    def _registration(self):
        st = getattr(self.nav, "state", None)
        if st is not None and not getattr(st, "ready", True):
            return "slam_not_ready", None
        got = self.reg_provider() if self.reg_provider else None
        if not got:
            return "no_registration", None
        T, gen = got if isinstance(got, tuple) else (got, None)
        live_gen = getattr(st, "map_gen", None)
        if gen is not None and live_gen is not None and gen != live_gen:
            return "map_reset", None                              # T belongs to a map that no longer exists
        if live_gen != self._map_gen:
            if self._map_gen is not None:
                self.forget("map_gen changed")
            self._map_gen = live_gen
        return None, SimpleNamespace(T=T, map_gen=gen)

    def _fresh(self, reg) -> set[tuple[int, int]] | None:
        area = getattr(self.nav, "area", None)
        if area is None:
            return None
        from perception import bb_source
        return bb_source.fresh_blocks(area, reg, self.fresh_s)

    def _new_passes(self, reg, now: float) -> set:
        fresh = self._fresh(reg)
        if fresh is None:                                          # no area map: the room is one block
            fresh = {WHOLE_ROOM}
        new = {b for b in fresh
               if b not in self._last_pass or b not in self._was_fresh or now - self._last_pass[b] >= self.pass_gap_s}
        for b in new:
            self._last_pass[b] = now
        self._was_fresh = set(fresh)
        return new

    def _block_of(self, e: Entry, head_recs: dict, reg):
        area = getattr(self.nav, "area", None)
        if area is None:
            return WHOLE_ROOM
        rec = None
        f = self.repo.path / e.path
        if f.is_file():
            try:
                rec = from_yaml(f.read_text())
            except SchemaError:
                rec = None
        rec = rec or head_recs.get(e.object_id)                    # a deleted object: where it used to be
        if rec is None:
            return None
        from perception import bb_source
        return bb_source.block_of((rec.pose.x, rec.pose.y, rec.pose.z), area, reg)

    def _signature(self, e: Entry) -> str:
        f = self.repo.path / e.path
        body = f.read_bytes() if f.is_file() else b""
        return change_type(e) + ":" + hashlib.sha1(body).hexdigest()[:12]

    def _age(self, block) -> float | None:
        area = getattr(self.nav, "area", None)
        if area is None or block is None or block == WHOLE_ROOM:
            return None
        try:
            from perception import bb_source
            age = bb_source._area(area).age
            v = float(age[block[1], block[0]])
            return None if v < 0 else round(v, 1)
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    def _stale_count(self) -> int:
        area = getattr(self.nav, "area", None)
        if area is None:
            return 0
        from perception import bb_source
        age = bb_source._area(area).age
        return int(((age < 0) | (age > self.fresh_s)).sum())

    def _restate(self, now: float, blocked: str | None) -> RoomState:
        s = self.state
        return RoomState(clean=s.clean, head=s.head, branch=s.branch, confirmed=s.confirmed, pending=s.pending,
                         stale_blocks=self._stale_count() if blocked is None else s.stale_blocks, at=_iso(now),
                         last_verified_job=s.last_verified_job, ignored=s.ignored, passes=self._passes,
                         blocked=blocked)

    # ── acting on it ────────────────────────────────────────────────────────────────────
    def _in_flight(self) -> set:
        fn = getattr(self.jobs, "in_flight", None)
        try:
            return set(fn()) if callable(fn) else set()
        except Exception:  # noqa: BLE001
            return set()

    def _busy(self) -> bool:
        fn = getattr(self.jobs, "busy", None)
        return bool(fn()) if callable(fn) else False

    def _job_running(self, job_id: str) -> bool:
        fn = getattr(self.jobs, "running", None)
        return bool(fn(job_id)) if callable(fn) else False

    def _unfixable_reason(self, oid: str) -> str | None:
        fn = getattr(self.jobs, "unfixable", None)
        try:
            return fn(oid) if callable(fn) else None
        except Exception:  # noqa: BLE001
            return None

    def _job_failed(self, job_id: str | None) -> bool:
        fn = getattr(self.jobs, "failed", None)
        return bool(job_id and callable(fn) and fn(job_id))

    def _act(self, confirmed: list[dict], now: float) -> None:
        todo = []                                                  # the robot's own hands: tidy | lost_and_found
        for c in confirmed:
            sig = self._seen.get(c["object_id"], {}).get("sig")
            did = self._acted.get(c["object_id"], {})
            if did.get("sig") == sig:
                # once per change, not once per tick. But a job that ENDED in failure leaves the mess where it
                # is: after retry_failed_s it is asked for again, rather than forgotten with a red badge
                if not (self._job_failed(did.get("ids", {}).get("job_id")) and now - did.get("at", now) >= self.retry_failed_s):
                    continue
            ids: dict[str, str] = {}
            why = self._unfixable_reason(c["object_id"])
            if why:                                                # the robot has proved it cannot: ask a person
                c["action"], c["why"] = "chore", why
            if c["action"] == "chore":
                chore, new = chores.open_chore(self.repo, c, _iso(now))
                ids["chore_id"] = chore["id"]
                self._job_objects.setdefault(chore["id"], set()).add(c["object_id"])
                if new and self.publish:
                    self._safe(self.publish, "chore", chore)
                if new and self.jobs and not self._busy():         # Tier B: drive up, face it, say it
                    visit = self._safe(self.jobs, "chore", {**c, **ids})
                    if visit:
                        ids["visit_id"] = str(visit)
                self._acted[c["object_id"]] = {"sig": sig, "ids": ids, "at": now}
                c.update(ids)
            elif self.jobs:
                todo.append((c, sig))
        if todo and not self._busy():                              # one job at a time: the rest wait for a later pass
            batch = getattr(self.jobs, "batch", None)
            groups = [todo] if callable(batch) else [[t] for t in todo]
            for group in groups:                                   # a hook that can, takes them all as ONE job: "2 of 3"
                rows = [c for c, _ in group]
                job_id = self._safe(batch, rows) if callable(batch) else self._safe(self.jobs, rows[0]["action"], rows[0])
                if not job_id:
                    break
                self.note_job(str(job_id))
                for c, sig in group:
                    self._acted[c["object_id"]] = {"sig": sig, "ids": {"job_id": str(job_id)}, "at": now}
                    c["job_id"] = str(job_id)
                    self._job_objects.setdefault(str(job_id), set()).add(c["object_id"])
                if self._busy():
                    break
        for oid in [o for o in self._acted if o not in self._seen]:
            del self._acted[oid]

    def _close_fixed(self, confirmed: list[dict], pending: list[dict], now: float) -> None:
        still = {(c["object_id"], c["type"]) for c in confirmed + pending}
        for ch in chores.list_chores(self.repo, "open"):
            if (ch["object_id"], ch["type"]) not in still:         # a fresh pass no longer shows it
                done = chores.close_chore(self.repo, ch["id"], "rescan", _iso(now))
                if done:
                    self.note_job(done["id"])
                    if self.publish:
                        self._safe(self.publish, "chore", done)

    @staticmethod
    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception:  # noqa: BLE001  an output that fails must not stop the room being watched
            return None

    def _emit(self) -> RoomState:
        if self.heartbeat:
            self._safe(self.heartbeat, self.state)
        v, now = self.state.verdict(), self.clock()
        # on CHANGE, and otherwise every republish_s: a dashboard cannot tell a loop that has been quietly clean
        # for an hour from one that died an hour ago, and the room's badge is only worth its timestamp
        if self.publish and (v != self._published or (self.republish_s and now - self._published_at >= self.republish_s)):
            self._published, self._published_at = v, now
            self._safe(self.publish, "room_state", self.state.to_dict())
        return self.state

    # ── forever ─────────────────────────────────────────────────────────────────────────
    def run(self, every_s: float = 1.0, stop: Callable[[], bool] | None = None, patrol: bool = True) -> None:
        """Tick about once a second. Keeps the robot patrolling (patrol never resumes by itself after a
        /navigate), unless this tier's robot does not move (C) or the loop was told not to act."""
        while True:
            t0 = time.monotonic()
            if patrol and self.act and self.tier != "C":
                self._keep_patrolling()
            self.tick()
            if stop and stop():                                  # asked after a tick: there is always a verdict to show
                return
            time.sleep(max(0.0, every_s - (time.monotonic() - t0)))

    def _keep_patrolling(self) -> None:
        nav = self.nav
        if getattr(nav, "area", None) is None or not hasattr(nav, "patrol") or self._busy():
            return                                                 # never drive off in the middle of a pick
        try:
            job = nav.job()
            if job is None or not job.running:
                nav.patrol()
        except Exception:  # noqa: BLE001  the robot being away is reported by `blocked`, not by a crash
            pass
