# The two minute video

Draft. Spoken words only, about 270 of them, which is two minutes at a calm pace. Shot notes are in
brackets. Nothing here is a claim we cannot show on screen; the measured numbers are in
[`DEMO-RUNBOOK.md`](DEMO-RUNBOOK.md).

---

**0:00 to 0:12 · the hook**
[The robot on the bench, clutter around it. Then a terminal.]

> This is a room under version control. Not a metaphor. A real git repository, where the working
> tree is the physical space in front of you.

**0:12 to 0:32 · git status of a room**
[Move the mug by hand. Type `room status`. The diff prints.]

> I move a mug. The robot scans, and `git status` says the mug moved eighteen centimetres.
> `git diff` prints it as a real diff, because the objects really are files, and the history really
> is git.

**0:32 to 0:58 · the robot is git apply**
[`room watch` running. The badge goes red. The arm picks the mug up and puts it back. Badge green.]

> Nothing else has to happen. The room notices the change, decides it is drift rather than a
> decision, and puts it back. Then it scans again, and the job only counts as done once a fresh
> look agrees. The room is clean because it was checked, not because the robot said so.

**0:58 to 1:14 · drift versus a decision**
[Move the lamp. Click "I meant that". The pull request opens and merges.]

> But sometimes I moved it on purpose. So I say so, and that becomes a pull request. Once it is
> merged, the lamp lives there, and the robot leaves it alone. Mess gets tidied. Decisions get
> merged.

**1:14 to 1:40 · ask the room**
[The panel. Type each line, let the answers land.]

> Git cannot tell me where my keys are, so the room is indexed in Elasticsearch as well.
> "The thing I cut paper with" finds the scissors, with no keyword in common.
> "Pick up the trash" is refused, because there is no trash, and a search that always returns its
> nearest guess would have sent a robot after a cup.
> And "where is the marker" answers: not here any more, it was on the desk at this commit, and
> gone by this one.

**1:40 to 1:56 · when it goes wrong**
[The telemetry page. The capture's numbers, then the Sentry trace.]

> When a scan is wrong, the room can say why: this capture's cameras were two milliseconds apart,
> the robot was steady, and here is the trace through Sentry that proves it. Sentry's own agent
> reads our errors and our code together, and it found a crash in this demo tonight.

**1:56 to 2:00 · close**
[The robot, idle, in a tidy room.]

> Git for the room you are standing in.

---

## Notes for the edit

* Beat 2 takes about half a minute in real time. Cut or speed the drive, never the verification:
  the rescan is the point.
* Every sentence in beat 4 is real output. Do not re-record them by hand.
* If the robot is unavailable, the same beats run on the simulator with one command, and say so on
  screen rather than letting it look like hardware.
* Do not quote a naming rate, and do not claim the cells are 3.125 cm unless the capture on screen
  was taken after the flip.
* The camera view of a public space should not be in the video if people are recognisable in it.
