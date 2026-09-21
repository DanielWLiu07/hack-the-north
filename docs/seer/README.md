# `docs/seer/` — what Sentry's agent found in our code

Seer read our traces, the breadcrumbs attached to each issue, and this repository, and said what was
wrong. One file per issue, written by `scripts/seer_sweep.py`.

**Nothing here was applied.** The sweep stops Seer at a root cause; it never asks for code changes or
a pull request, and it refuses to start with a stopping point that would. A finding is a document a
person reads, and a person routes to whoever owns the file. Where Seer says an issue is ALREADY FIXED
in a later commit, the issue wants resolving in Sentry — not another edit.

23 issue(s) collected, newest sweep 2026-09-21T12:51:13+00:00.

| issue | what it is | what Seer found | files it named |
|---|---|---|---|
| [GITSPACE-13](GITSPACE-13.md)  | AttributeError: module 'builtins' has no attribute 'ELASTIC_URL' | This design infers the server module (which holds ELASTIC_URL, ELASTIC_API_KEY, ApiError) by looking up type(es).__module__ — the __module__ of w… | `web/es_shared.py` |
| [GITSPACE-15](GITSPACE-15.md)  | robot: camera_unavailable — cameras up: [] · unavailable: {'cam0': 'bb | Root cause: the head stereo camera is physically off the USB bus. | `robot/bbos.py` |
| [GITSPACE-19](GITSPACE-19.md) **already fixed** | ValueError: '192.168.0.30   # the laptop (wifi + tailnet) and this rob | 1. The .env file had an inline comment on the ROBOT_ALLOW line (e.g. 192.168.0.30 the laptop (wifi + tailnet) and this robot itself). This is har… | `robot/allow.py` |
| [GITSPACE-1B](GITSPACE-1B.md)  | robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no w | - The bbos camera daemon loops forever with FileNotFoundError: could not find camera 'USB Camera' | `robot_sentry_watch.py` |
| [GITSPACE-1D](GITSPACE-1D.md)  | Cron failure: room-clean | 1. ROOM_CLEAN_CRON=1 is set in the environment, and | `robot_sentry.py`, `scripts/room_clean_beat.py` |
| [GITSPACE-1E](GITSPACE-1E.md)  | robot: housebot_failed — robot API failed; delivery status may be unkn | Root cause identified: The robot adapter service (scripts/run_robot_api.py, expected on port 8765) is not running. | `scripts/run_robot_api.py`, `andrew/README.md` |
| [GITSPACE-1F](GITSPACE-1F.md) **already fixed** | KeyError: 'detail' | When the confirm action kind was introduced (to handle ambiguous object matches scoring between 1.11–1.20 in Elasticsearch), the _outcome() funct… | `bridge/agent_api.py` |
| [GITSPACE-1G](GITSPACE-1G.md)  | robot: object_not_found — nothing in the room answers 'pick up the tra | Root cause: The error is intentional, working-as-designed behavior. | `elastic/queries.py` |
| [GITSPACE-1H](GITSPACE-1H.md)  | robot: bbos_silent — bbos's SLAM publishes nothing (slam.pose has no w | 1. bbos's camera daemon loops with FileNotFoundError: could not find camera 'USB Camera' every 2 s — it can't open the device | `robot_sentry_watch.py`, `docs/35-robot-access.md` |
| [GITSPACE-1J](GITSPACE-1J.md) **already fixed** | NameError: name 'log' is not defined | Root cause: In perception/pipeline.py, the new pose-validation branch introduced in commit ba2dc77 called log.warning(...) to report that a captu… | `perception/pipeline.py` |
| [GITSPACE-1K](GITSPACE-1K.md) **already fixed** | robot: map_reset — the robot's map generation changed 3971697493 -> 16 | 1. Head stereo camera dropped off the USB bus (issues [GITSPACE-1H](https://na-alh.sentry.io/issues/7743510975/?referrer=seer.agent.in-chat-link)… | `constants.py`, `slam/daemon.py`, `robot_sentry_watch.py` |
| [GITSPACE-1N](GITSPACE-1N.md)  | KeyError: 'query' | So body["query"] raises a KeyError because query lives at body["body"]["query"], not at the top level. The same applies to body.get("highlight") … | `telemetry_search_api.py` |
| [GITSPACE-4](GITSPACE-4.md)  | CancelledError: Task cancelled, timeout graceful shutdown exceeded | Root cause: The /api/events SSE endpoint returns a StreamingResponse with Connection: keep-alive that stays open indefinitely, waiting for the cl… | — |
| [GITSPACE-5](GITSPACE-5.md)  | Cancel 1 running task(s), timeout graceful shutdown exceeded | Root cause: watch_room() (in web/events.py) polls git by calling await asyncio.to_thread(room.snapshot) in a tight loop. room.snapshot() runs git… | `web/events.py`, `web/server.py` |
| [GITSPACE-6](GITSPACE-6.md)  | robot: fell_over — balanced went 0; pitch 0.350199, peak tilt_rate 0.6 | The robot falls because the balance controller loses effectiveness due to undervoltage, while the drive.state.iq bbos bug hides all evidence of m… | `robot/NOTES.md`, `robot/bbos.py` |
| [GITSPACE-7](GITSPACE-7.md)  | robot: grasp_slipped — gripper closed to 2mm, expected 78mm — target f | 1. cap_live01 was taken while the robot was actively leaning — tilt_rate_max = 0.239 rad/s, nearly 5× the 0.05 rad/s threshold | `robot/capture.py` |
| [GITSPACE-C](GITSPACE-C.md)  | Downtime detected for https://gitirl.health/api/health | Root cause: The gitirl.health/api/health uptime monitor is firing because the FastAPI web server (web/server.py) that backs it is no longer runni… | `web/server.py` |
| [GITSPACE-D](GITSPACE-D.md)  | robot: camera_unavailable — /dev/v4l/by-path/x did not open | The 6 occurrences of camera_unavailable — /dev/v4l/by-path/x did not open are not real hardware failures — they're noise generated by a unit test. | `tests/test_robot_capture.py` |
| [GITSPACE-N](GITSPACE-N.md)  | AssertionError: no horizontal plane: cam_to_world_axes missing or appl | The room commit fails because fuse.assert_floor() finds no horizontal plane in the world-frame point cloud from capture cap_0006. The transform c… | `capture_to_recording.py` |
| [GITSPACE-Q](GITSPACE-Q.md)  | OverflowError: Python integer -1 out of bounds for uint16 | 1. test_depth_is_never_paired_with_a_colour_frame_it_does_not_belong_to sets world.skew = 1 before any tick() call, so world.n = 0. | `test_robot_bbos.py` |
| [GITSPACE-R](GITSPACE-R.md)  | AssertionError: lowest horizontal plane is at z=-0.055 m, not 0: check | These values are correct for bbos's own rectified frame but wrong for this project's rectification (stereo_calibration_fisheye.yaml). The code it… | `capture_to_recording.py` |
| [GITSPACE-W](GITSPACE-W.md)  | robot: camera_unavailable — cam0: preview unavailable (HTTP 503, no va | - ~97% of 50 Hz telemetry ticks were skipped in 30 seconds (+1,465 overruns), indicating near-total CPU saturation | `robot_sentry_watch.py` |
| [GITSPACE-X](GITSPACE-X.md)  | robot: telemetry_unfed — tilt_rate is null in every sample — the IMU r | 1. robot_sentry_watch couldn't reach the robot's /telemetry endpoint, so sample_telemetry() returned all-None tilt values. | `robot/server.py` |
