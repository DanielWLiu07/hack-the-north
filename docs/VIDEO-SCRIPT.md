# The two minute video

Draft two. Opens on the robot doing the physical thing, then earns the three tracks in order:
Bracket Bot, Elasticsearch, Sentry. Git arrives when the site does, not before.

About 285 spoken words, which is two minutes at a calm pace. Shot notes in brackets. Nothing here
is a claim we cannot show; the measured numbers are in [`DEMO-RUNBOOK.md`](DEMO-RUNBOOK.md).

---

**0:00 to 0:22 · cold open, no narration for the first five seconds**
[A crumpled snack wrapper on the floor. The robot turns, drives to it, picks it up, drops it in the
bin, and backs off. Real hardware, one continuous shot, no cuts.]

> Nobody asked it to do that.

[Beat.]

> It looked at the room, saw something that was not where the room says it should be, and put it
> away. This is a Bracket Bot, and this is the whole product in one shot.

**0:22 to 0:48 · Bracket Bot: what that actually took**
[Split screen: the robot, and its own fused voxel map filling in as it turns.]

> It is not using our map. It is using the robot's own SLAM map, three centimetre voxels, read
> straight off the machine, with every capture placed by the pose the robot had at the shutter.
> And it refuses more than it accepts. Wrong map generation, no motion. A stance at the very end of
> its arm's reach, refused. An object the room no longer has, refused. A wrapper four centimetres
> tall against six centimetres of floor noise is invisible to height, so we find it by colour:
> twelve of twelve real items across twenty captures, one false positive.

**0:48 to 1:14 · Elasticsearch: the room you can ask**
[The site. Type the lines, let the answers land.]

> Everything it has ever seen is in Elasticsearch. "The thing I cut paper with" finds the scissors,
> sharing no word with it. "Pick up the trash" is refused, because there is no trash, and a vector
> search always returns its nearest guess, which would have sent a robot after a ceramic cup.
> "Where is the marker" answers: not here any more, it was on the desk at this commit, and gone by
> this one. The room remembers what it no longer has.

**1:14 to 1:38 · and now the git part**
[The history graph. Move the lamp, click "I meant that", the merge lands.]

> Because underneath, the room is a real git repository. Objects are files, a scan is a commit,
> and this graph is the room's actual history. Mess is drift and the robot tidies it. But when I
> move something on purpose, I say so, that becomes a pull request, and once it is merged the robot
> leaves it alone.

**1:38 to 1:56 · Sentry: how we know any of this is true**
[Telemetry page: the capture's gate numbers, then a trace spanning laptop and robot, then Seer.]

> One trace runs from this browser to the laptop to the robot. When a scan is wrong, the room says
> why: the cameras were two milliseconds apart, the robot was steady. Sentry's agent reads our
> errors and our repository together, and it found a real crash in this demo tonight.

**1:56 to 2:00 · close**
[The robot idle, the floor clear.]

> A room that tidies itself, and can prove it did.

---

## Notes for the edit

* **The cold open is the whole video.** If the pick is not proven on hardware by filming time, the
  honest substitute is Tier B: the robot drives to the wrapper, points at it, says it cannot lift it
  and files the chore. Shoot whichever is real. Do not stage a pick that did not happen.
* Nothing in the first twenty seconds should mention git. The physical act earns the attention;
  the repository explains it afterwards.
* Beat 2's tidy and rescan take about half a minute in real time. Cut or speed the drive, never the
  verification.
* Every sentence in the Elasticsearch beat is real output. Do not re-record them by hand.
* Do not quote a naming rate. Do not claim 3.125 cm cells unless the capture on screen was written
  after the flip.
* Keep recognisable bystanders out of any camera view, and out of the point clouds on screen.
