# PROGRESS.md — append-only build log

**Every session appends here when it finishes a numbered step.** Newest at the bottom.
This file feeds three things: the Devpost writeup, the Sentry story, and the answer to
"what would we demo right now if judging started".

Format — one block, no prose paragraphs:

```
## h<NN> · <track> · <what landed>
Files:      <paths>
Verified:   <the command you ran and what it printed>
Blocked on: <or "nothing">
Surprise:   <anything you expected to be true and wasn't — this is the valuable line>
```

The **Surprise** line is the one that matters. It is where the Devpost's interesting
content comes from, and if observability found it, it also belongs in `SENTRY_STORY.md`.

---

## h00 · setup · six parallel sessions running, three interfaces frozen
Files:      docs/ (22 docs), TASK-*.md per track, .env, obs.py
Verified:   `tmux list-windows -t htn` → 8 windows; Sentry smoketest landed
Blocked on: nothing
Surprise:   Bracket Bot's own depth and odometry modules disagree about what X and Z
            mean (X-right/Z-fwd vs X-fwd/Z-left). Composing them naively rotates every
            cloud 90°, and it only shows once the robot turns — so it reads as drift.
            Documented in docs/20-perception-logic.md.

## h00 · observability · Sentry ↔ Elasticsearch bridge working
Files:      obs.py, scripts/story_demo.py, SENTRY_STORY.md
Verified:   `python3 scripts/story_demo.py` → trace 50c0ccf2…, issue 7741490949,
            4 ES docs each carrying sentry_trace_id
Blocked on: tracing/Performance may be plan-gated — needs a UI check
Surprise:   The auth token had a stray leading `y` (72 chars, not 71). Sentry returns
            "Invalid token" for a malformed token, not "malformed" — one API call
            succeeded before the re-paste, which made it look intermittent.

## h00 · cloud · laptop bootstrap proves the critical path runs with no internet
Files:      scripts/bootstrap_laptop.sh, scripts/check_offline.py, scripts/requirements-laptop.txt,
            .env.example (+MODELS_DIR)
Verified:   `scripts/bootstrap_laptop.sh` → 116 packages, 7 YOLO weights, offline check 13/13;
            `--offline --recreate` rebuilt .venv from the uv cache in 70 s, 13/13 again
Blocked on: ELASTIC_URL in .env is still the .env.example placeholder (the API key is real)
Surprise:   BB's `YOLO("yolo11s-seg.pt")` silently downloads into cwd when the file is missing,
            and ultralytics' SAM 3 loader pip-installs CLIP from GitHub on first use — two
            network calls hiding on the critical path. Weights now resolve from $MODELS_DIR, and
            YOLO_OFFLINE=1 in the venv makes a missing weight fail fast instead of hanging.

## h00 · observability · obs.init wired into web + perception; tracing is NOT plan-gated
Files:      web/server.py, perception/depth.py (__main__ only), web/requirements.txt,
            scripts/requirements-pi.txt
Verified:   web on :8799 → `GET /` transaction (server_name=web) + uvicorn logs in Sentry Logs;
            `depth.py --help` → client active, server_name=laptop, traces/profiles 1.0, logs on;
            API: story_demo trace 50c0ccf2 has all 7 spans; plan am3_t includes spans/profiles/logs
Blocked on: robot/ has no Python yet — obs.init("pi") goes in when robot/server.py lands
Surprise:   An empty Performance tab is not plan-gating. On AM3, transactions aren't indexed
            (`transaction_indexed: discarded`); they live in the spans dataset → Explore → Traces.
            And the default health-check filter drops every /api/health transaction (6 filtered,
            reason `filtered-transaction`) — never verify tracing with a health check.

## h00 · elastic · setup_elastic.py + 6 mappings with the Sentry join + ingest.py
Files:      elastic/setup_elastic.py, elastic/mappings/*.json (6), elastic/ingest.py, elastic/NOTES.md
Verified:   `python elastic/setup_elastic.py --check` → "mappings 6 files valid". All 5,808 docs in
            fake/out/demo.ndjson + the 4 in story_docs.json pass an offline strict-mapping check
            (0 rejects, 0 TSDS identity collisions after ms truncation). Two runs against an
            in-memory fake cluster: run 2 creates nothing.
            NOT live: `python elastic/setup_elastic.py` → "error: ELASTIC_API_KEY is a URL, not an API key"
Blocked on: ELASTIC_API_KEY in .env is a web page URL (the system design page),
            not a key. Live setup, the story_docs smoke index and the "mug" acceptance wait on it.
Surprise:   The "4 ES docs each carrying sentry_trace_id" from h00 observability never reached
            Elasticsearch: story_demo.py only writes story_docs.json, and the key has never worked.
            Sent as-is they'd be rejected anyway, because each doc carries `_index` inside its body
            (a metadata field). elastic/ingest.py moves it to the action line.

## h00 · perception/pointcloud · depth.py: stereo pair → (H,W,3) METRES, pixel-aligned to left_rect
Files:      perception/depth.py, perception/tests/test_depth.py
Verified:   `python3 -m pytest perception/tests/test_depth.py` → 8 passed. Synthetic fisheye rig
            ray-traced at a plane: geometry exact to ~2 mm at whole-pixel disparity; a red marker
            found in left_rect lifts via xyz[mask & valid] to its true 3-D spot; a doubled and a
            missing /1000 both trip the metres assertion. 6 deliberate mutations, all caught.
Blocked on: the robot's real stereo_calibration_fisheye.yaml + one frame, for "a 1 m object
            measures 1 m": `python perception/depth.py calib.yaml frame.jpg --px u1,v1 --px u2,v2`
Surprise:   BB's depth example keeps points BEHIND the camera: MIN_DISP=-32 admits negative
            disparities, CALIB_ZERO_DISPARITY reprojects those behind the lens, and its 5 m cull
            keeps them (swapped eyes → 46% of pixels become a mirrored room 1 m behind the robot).
            Also: SGBM pixel-locks up to 0.25 px toward whole disparities, ~2 cm at 1 m at
            DOWNSAMPLE 0.375 — the same size as the 1 cm quantum. And docs/20's `nanmax < 50`
            can't catch a DOUBLED /1000; a median-range band (0.02–50 m) catches both.

## h00 · perception/segment · cluster.py — fallback path: plane removal ×4 → DBSCAN, planes kept as zones
Files:      perception/cluster.py, perception/tests/test_cluster.py
Verified:   `python3 -m pytest perception/tests/test_cluster.py` → 15 passed. Box + mug on a table
            → 2 instances within 2 cm, floor + table kept as planes (table height within 1 cm).
            Plane removal off → 0 instances (tabletop joins everything into one blob). Rescans
            with fresh noise agree to < 5 mm / < 2.5°. 1.7M points in 2.3 s.
Blocked on: nothing for the code; no real capture yet (needs depth.py → fuse.py wired)
Surprise:   (1) docs/15's DBSCAN min_points=40 only works while stereo noise makes surfaces two
            voxels thick. On a clean one-voxel surface a point has 22–28 neighbours, so every
            point is noise: it breaks as depth gets BETTER. Split into core density 10 plus a
            40-point minimum object size.
            (2) Principal-axis (PCA) yaw wanders ~12° between rescans on a 13×10 cm box, more than
            the 7.5° dead-band, so every scan would dirty git. cv2.minAreaRect is exact on clean
            points, but 0.5% stray points swing it by up to 90°. A min-area search that ignores
            the outer 1% of points holds ±1°. The frozen schema's yaw became an AXIS in
            [0,180) to match what geometry can actually measure.
            (3) The repo .venv has no sklearn, so DBSCAN is reimplemented on scipy and tested
            equal to sklearn on core and noise points.

## h05 · web · server.py proxy up; landing page rebuilt as the GITRL "everything is watching you" scene
Files:      web/server.py, web/requirements.txt, web/LANDING-TASK.md, web/landing/{scene,layout,mech,
            watchers,heads,hall,title,tentacles}.js, web/landing/vendor/three/, web/landing/tools/build_gitrl.py
Verified:   `python3 server.py` → listens on *:8000 per WEB_BIND; `/` serves landing/, `/api/*` keeps the §2.7
            error shape, `/serve.py` `/..%2F.env` → 404; ES key/URL appear in no response body.
            Headless Chrome 2560x1440, every module loaded: 60 fps, 972 draw calls, 410k tris, 0 console errors.
            Motion audit (31 rigged chains, 22 s, real fast pointer): worst joint step 16.9°/frame, 0 NaN.
Blocked on: Elasticsearch — ELASTIC_URL host does not resolve and ELASTIC_API_KEY holds a URL, not a key, so
            /api/search (the acceptance test: "mug" → "ceramic cup" via the vector leg) cannot be verified yet.
            NOT built yet: /api/search, /api/status, /api/events, dashboard sections, /capture/<id>.
Surprise:   (1) Katie Roze's glyph outlines are EMPTY — it is a colour font, every letter is a watercolour PNG
            inside the SVG table — so "extrude the font" produced nothing; the 3D title is traced from that
            embedded art (tools/build_gitrl.py), keeping the font's own advance widths.
            (2) The page loaded three.js from unpkg; one network drop made the whole landing page blank in
            headless capture. It is vendored now — the hero makes zero external requests (venue wifi).
            (3) Background jobs die when the Mac sleeps; five died at once when the lid/idle sleep
            hit. Work that must finish has to run in a session that is actively making tool calls.
            (4) "The animations freak out" was NOT solver instability (the audit proves the chains are smooth):
            it was behaviour — losing window focus counted as "the visitor left", so the crowd started
            looking at each other at random while the user typed in another window.

## h00 · perception/pointcloud · fuse.py: cam_to_world_axes + floor assertion (Z-up, floor at z=0)
Files:      perception/fuse.py, perception/tests/test_fuse.py
Verified:   `python3 -m pytest perception/tests/test_fuse.py` → 17 passed. Points built from first
            principles land exactly (1e-9 m) at their world position for 4 mounts incl. ±120° yaw;
            matches BB's own R_x(-36)+[0,-1.5,0] math up to the X-fwd/Y-left relabel; floor found
            at |z|<1 cm with 5 mm noise + 0.5% mismatches, wall normal within 2° of X at 4.00 m.
            The floor assertion fires on: axes missing, axes twice, Z flipped, pitch sign, height
            +20 cm, mm-not-m. A test pins `cam_to_world_axes` to one def + one call repo-wide.
            6 deliberate mutations of fuse.py, all caught.
Blocked on: the stock camera's MEASURED pitch and height (BB's 36°/1.5 m are another rig's)
Surprise:   The floor assertion is blind to a left/right MIRROR (x → +y instead of −y) and to a
            yaw sign error: the floor stays at z=0 either way. Only the handedness/round-trip
            tests catch those — the floor check is necessary, not sufficient.

## h00 · perception/pointcloud · serialize.py: stabilize() = quantize 1 cm/5° AND 1.5-quantum hysteresis
Files:      perception/serialize.py, perception/tests/test_serialize.py
Verified:   `python3 -m pytest perception/tests/test_serialize.py` → 21 passed. In a real git repo, 25
            rescans of a 5-object room with EVERY value on a bucket edge (0.425 m, yaw 2.5°/177.5°,
            ±4 mm / ±3° jitter): `git status --porcelain -uall` empty each time. 20 cm move → exactly
            ` M zones/desk/book_c3d4.yaml`. Occluded object carried forward, removed one deleted.
            docs/20's 0.4250 m ± 0.5 mm: quantize-only writes both 0.42 and 0.43; stabilize writes one.
            Constants, ObjectRecord and write_tree all from roomctl.state. 6 mutations, all caught.
Blocked on: associate.py (stable ids feed serialize.Measured); perception/pipeline.py scan_into() for
            the G2 gate in tests/test_idempotent_scan.py — in neither perception TASK file yet.
Surprise:   The fold into [0, 180) must come AFTER rounding: 178° rounds to 180, which the schema
            rejects. And a plain-subtraction deadband calls -89° vs 89° (the same axis, 2° apart) a
            178° turn — the mutation test that removed yaw_diff() dirtied the tree on scan 1.

## h00 · perception/pointcloud · obs.span on every stage + obs.capture_quality gate right after depth
Files:      perception/depth.py (depth_capture, coverage), perception/fuse.py, perception/serialize.py,
            perception/tests/test_trace.py, perception/tests/test_depth.py
Verified:   `python3 -m pytest perception/tests` → 95 passed (incl. the segment track's cluster tests).
            test_trace.py runs depth → fuse → serialize under a capturing Sentry transport (no network)
            and checks the waterfall: perception.stereo{rectify, sgbm, reproject} per camera,
            capture_gate {skew_ms 1.4, tilt_rate_max 0.031, coverage, quality_ok}, fuse{floor_check
            floor_z}, serialize {n_changed — the phantom-diff gauge, 0 on an unchanged room}. The gate
            rejects 30 ms skew, 0.2 rad/s tilt, and a lens-capped camera. OpenCV 4.14 (.venv) and 5.0
            give the same depth (2.6 mm median error at whole-pixel disparity) and floor (z = 0).
Blocked on: nothing here. For obs.py's owner: see Surprise (2) and (3).
Surprise:   (1) Counted over the whole image, coverage can never pass 77%: SGBM can't match the leftmost
            MIN_DISP+NUM_DISP = 112 of 480 columns, so capture_quality's > 0.60 would reject most real
            scenes. coverage() counts matchable pixels instead (synthetic scene: 91% vs 70%).
            (2) capture_quality() sets `capture_rejected` on whatever scope is current; outside
            obs.capture_scope() that tag sticks to every later event in the process.
            (3) obs.span passes `description=`, deprecated in sentry-sdk 2.69 — wants `name=`.

## h00 · elastic · telemetry downsampling declared + queries.py (16 queries) + live tests for each
Files:      elastic/mappings/robot-telemetry.json, elastic/setup_elastic.py, elastic/queries.py,
            elastic/tests/{conftest,world,test_queries}.py, elastic/NOTES.md
Verified:   `setup_elastic.py --check` → "mappings 6 files valid" (now also enforces ES's downsampling
            rules). Offline: all 606 fixture docs + 8,859 fake/out/demo.ndjson docs pass the strict
            mappings, 0 _id / TSDS collisions, every deterministic test expectation holds on the data.
            `pytest tests` → 1 passed (every query has a test), 18 ERROR "live cluster unavailable".
            NOT live: .env ELASTIC_URL and ELASTIC_API_KEY are both empty since 21:12 (before that the
            key was a web page URL). No query has ever run against Elasticsearch.
Blocked on: a real ELASTIC_URL + Encoded API key in .env.
Surprise:   docs/23's "raw → 1 s after 1 h → 1 min after 6 h" can't be declared. The data stream
            lifecycle (all Serverless has) refuses rounds finer than 5 min, and `after` counts from
            ROLLOVER, which with no retention set is 30 days away, so nothing would have been downsampled
            this weekend. Declared: retention 7d (→ daily rollover), 1h → 5m, 6h → 30m. Peaks still
            survive because downsampled gauges keep min/max/sum/count. Second one: the Jina reranker
            reads only the FIRST value of a multi-valued field, so it judges each object on one of its
            three VLM descriptions (retrieval still uses all three). Details in elastic/NOTES.md.

## h06 · web · /api/search with real match provenance + the status and search sections of `/`
Files:      web/dash_api.py (GET /api/search, GET /api/object/{id}), web/landing/dash.js, web/landing/dash.css
Verified:   MOCKS ONLY — Elasticsearch is unreachable, the real retriever has never run.
            `pytest test_dash_api.py` (scratchpad, mock cluster fed from fake/out/demo.ndjson) → 9 passed:
            "mug" → mug_a1b2 {bm25 ✓, vector ✓, rerank #1} + cup_7e21 {bm25 ✗, vector ✓, rerank #2}, no cup
            description contains "mug"; hammer found and present_now:false; present-only filter; no-reranker
            fallback says `reranked:false`; unreachable ES is surfaced, not swallowed.
            In-process against the real server.py → "router dash_api: loaded (2 routes)", every failure in the
            §2.7 shape, ES key/URL in no response. Headless Chrome 1440x900 + 390x844 on a harness running the
            REAL router: the vector-only card is the only orange on the page, no horizontal overflow, SSE live.
Blocked on: a working ELASTIC_URL / ELASTIC_API_KEY. Until then retriever syntax, the reranker on a multi-valued
            semantic_text field, and the two score cuts (SEARCH_MIN_RERANK_SCORE 0.08, SEARCH_RELATIVE_CUT 0.2)
            are unproven. The running :8000 server predates the router hook — restart it to get /api/search.
Surprise:   `raw_description` is mapped semantic_text ONLY, and Elasticsearch rewrites a `match` on a
            semantic_text field into a SEMANTIC query — so the obvious "BM25 leg" (match on class + description)
            would find "ceramic cup" for "mug" and the vector-only badge would silently never appear. The lexical
            leg uses only fields the live mapping types as `text` (today: `class`); if elastic/ wants BM25 over
            descriptions it needs a `text` sibling (copy_to / multi-field), which dash_api then picks up by itself.
            Second one, caught by our own test: kNN ALWAYS returns neighbours, so searching the present for the
            absent hammer returned all 11 objects at equal low scores — a relative cut alone passes them all.

## h00 · perception/segment · segment.py: masks on left_rect → xyz[mask & valid] → per-camera instances
Files:      perception/segment.py, perception/tests/test_segment.py
Verified:   `python3 -m pytest perception/tests/test_segment.py` → 8 passed, including ACCEPTANCE
            test_touching_mug_and_book_come_back_as_two_instances. On a ray-traced view where the mug
            touches the book, DBSCAN on the same points finds 1 blob and the masks give 2 instances
            within 1 cm. Real YOLO (yolo11s-seg, BB's conf/iou) on bus.jpg at 480×270: 5 masks, each
            bool 270×480 (retina_masks, aligned with left_rect); lift drops the 4 persons (.roomignore).
Blocked on: nothing for the code; no real stereo capture run through it yet
Surprise:   The lift really is one line; the work is at the mask EDGE. A 3 px mask overshoot on a
            mug reaches the wall 1.2 m behind it and drags the 3-D centroid. Fix: erode the mask 2 px,
            and drop points more than 30 cm from the mask's median depth. Also: the bootstrap had
            already put the weights in $MODELS_DIR/weights with ultralytics pointed there, so
            YOLO("yolo11s-seg.pt") resolves offline. Don't build a path to it.

## h00 · perception/segment · describe.py: one VLM description per instance PER CAMERA, never reconciled
Files:      perception/describe.py, perception/tests/test_describe.py
Verified:   `python3 -m pytest perception/tests/test_describe.py` → 6 passed. 3 views → 3 different
            descriptions, each attached to its own view; a failed call is retried and counted as
            label_attempt; a dead VLM records the error and the capture carries on. The OpenAI
            request (Responses API, json_schema, effort minimal) is checked on a captured fake request.
Blocked on: OPENAI_API_KEY is empty in .env, so no live VLM call has run (model = OPENAI_VISION_MODEL=gpt-5)
Surprise:   The natural prompt ("this was detected as a cup, describe it") destroys the thing we
            want. Once every view is handed YOLO's label, all three agree and the entity-resolution
            signal is gone. The VLM gets only the crop, and a test fails if the label leaks into
            the request. Also, room-observations is dynamic: strict, so the VLM's short label has no
            field of its own. Only raw_description goes to ES.

## h00 · perception/segment · merge.py: cross-camera merge that keeps every view's words and position
Files:      perception/merge.py, perception/tests/test_merge.py
Verified:   `python3 -m pytest perception/tests/test_merge.py` → 9 passed. 3 cameras → 1 object keeping
            3 descriptions. observations() gives one room-observations row per camera, with that
            camera's own raw_x (the /capture/<id> disagreement) and only fields in the strict mapping.
            Touching mug+book seen by 2 cameras → 2 objects. Mutation-tested across segment/describe/
            merge/associate: 10 deliberate breaks (collapse descriptions, merged centroid in rows,
            label hint to the VLM, no linkage…), all caught. Merge's two same-camera guards back
            each other up, so that break only fails once both are removed.
Blocked on: nothing
Surprise:   docs/15's merge rule would UNDO the touching-objects fix at stage 8. Two touching cups
            from one camera pass all three tests (centroids < 15 cm, same label, boxes overlap), so
            merge would glue back together what the masks separated. Added: never merge two views
            from the same camera, and complete linkage so A~B, B~C can't chain A with C 24 cm away.

## h00 · perception/segment · associate.py: the docs/20 Part 4 decision table, ids survive absences
Files:      perception/associate.py, perception/tests/test_associate.py
Verified:   `python3 -m pytest perception/tests/test_associate.py` → 8 passed, including ACCEPTANCE
            test_object_that_leaves_for_three_commits_and_returns_keeps_its_id. It runs through the real
            serialize.py + roomctl.state working tree: mug gone 3 commits, back 80 cm away in different
            words → `returned`, original id/first_seen/class/colour. Control: no history search →
            new id. Also: far move (>1.5 m) keeps its id, a different object in the same spot doesn't
            steal it, occluded → unobserved + byte-identical, near-square yaw keeps the committed axis.
Blocked on: (1) live ES: ELASTIC_URL/ELASTIC_API_KEY empty, so ESHistory's query has only been checked
            for shape. (2) zone assignment: nobody sets `zone` for a NEW object yet;
            for_serialize() raises rather than write a bad file. (3) elastic/queries.py
            search_objects is the same hybrid query. ESHistory should call it once it also returns
            each hit's extents/color/first_seen.
Surprise:   Hungarian-against-HEAD alone turns a 2 m move in one commit into a delete plus an add.
            The 1.5 m gate is also a history cliff. The same history search that finds `returned`
            objects catches it, so an object moved across the room keeps its id.

## h05 · web · browser Sentry with Session Replay that actually records the WebGL canvas
Files:      web/landing/sentry.js, web/landing/vendor/sentry/ (@sentry/browser 10.75.0 + replay-canvas,
            vendored so the page loads offline), web/landing/index.html (one script tag),
            web/server.py (`GET /api/config`, obs.span around every ES call), web/requirements.txt
Verified:   obs.init('web') was already in server.py; checked it is live: `client.is_active()` True,
            server_name web, and a test message + trace through obs → ingest HTTP [200, 200, 200].
            Browser, headless Chrome on the real page: integrations BrowserTracing + Replay +
            ReplayCanvas, envelopes session/transaction/replay all HTTP 200; replay segments
            242 / 412 / 346 KB each. `/api/config` returns 5 whitelisted keys and none of
            ELASTIC_API_KEY / SENTRY_AUTH_TOKEN / OPENAI_API_KEY / SENTRY_DSN appear in it.
            Cost at 2560x1440: 60.0 fps off vs 60.1 on, p99 frame 24.4 vs 24.0 ms.
Blocked on: nothing. Replay text is unmasked on purpose (no people stored); INPUTS stay masked
            unless an element carries `data-sentry-unmask` — the search box has to opt in.
Surprise:   The first replays were accepted with HTTP 200 and were black rectangles. Segment
            size gave it away: ~2 KB. replayCanvasIntegration's snapshot() defers to the NEXT
            animation frame by default, and by then WebGL (no preserveDrawingBuffer) has cleared
            the buffer; the integration drops blank frames silently, so nothing errors.
            `snapshot(canvas, { skipRequestAnimationFrame: true })` from the after-render hook
            fixed it: 2 KB → 240–410 KB per segment. Also: `beforeAddRecordingEvent` only ever
            sees CUSTOM events, so it cannot be used to count canvas frames — it reported 0
            while 10 of 10 frames were being encoded. "Sentry accepted it" is not "it recorded".

## h05 · web · `GET /api/events` (SSE) + `GET /api/status`, read straight from room.git
Files:      web/events.py (hub, ring buffer, room watcher), web/room.py (read-only git reader),
            web/server.py (`/api/status`, `/api/events`, loopback `POST /api/internal/event`,
            include_router hook for capture_api / dash_api, landing mount still last)
Verified:   `curl -N :8011/api/events` → `retry: 2000` then `event: status {"clean":true,"changes":0,…}`.
            `python fake/scene_gen.py --scan clean_bench` → within ~1 s `id: 1bc8b8-1 event: status
            {"clean":false,"changes":3,…}` and /api/status lists modified mug_a1b2 delta_m 0.19,
            deleted scissors_9f3a, untracked marker_c3d4; `--scan messy_bench` → clean again,
            `git -C room.git status --porcelain` empty. Reconnect with `Last-Event-ID: 1bc8b8-1`
            replays -2 and -3 then the current snapshot; an id from another boot replays nothing.
            Inlet: job + telemetry from 127.0.0.1 → `{"published":…,"clients":1}` and arrive on the
            stream (telemetry with no id, never replayed); same POST from the LAN address → 403,
            with `X-Forwarded-For` → 403, unknown event name → 400. SSE request is dropped from
            Sentry tracing (transactions sent for one SSE connection: []) while /api/status is traced.
            Ctrl-C with a stream open exits in 3.4 s, measured (`timeout_graceful_shutdown`).
Blocked on: `capture` events carry commit_sha only — room commits have no capture_id trailer to
            read. `job` and `telemetry` have no producer yet: roomctl's executor and the laptop
            ingest need to POST them to /api/internal/event.
Surprise:   A plain `git status` WRITES: it refreshes .git/index, so polling another session's
            repository once a second from a "read-only" dashboard is a write plus a lock roomctl can
            trip over mid-commit. Every call here is `git --no-optional-locks`. Second one: behind a
            reverse proxy every client's peer address is 127.0.0.1, so a loopback-only endpoint is
            open to the internet on the AWS tier unless it also refuses forwarding headers.

## h00 · elastic · BM25 now reads the descriptions (raw_description.text), no writer change
Files:      elastic/mappings/room-objects.json, elastic/mappings/room-observations.json,
            elastic/queries.py, elastic/setup_elastic.py, elastic/tests/test_queries.py
Verified:   `setup_elastic.py --check` → "mappings 6 files valid"; fake-cluster suite 28/28;
            demo.ndjson still 0 rejects. New live tests: lexical_only("porcelain") == [cup_7e21],
            lexical_only("mug") excludes cup_7e21 while hybrid "mug" has it in the top 3. Not run live.
Blocked on: ELASTIC_URL + ELASTIC_API_KEY (both empty in .env).
Surprise:   raised by web. `raw_description` was semantic_text only, and a `match` on
            semantic_text is silently rewritten to a semantic query. So "BM25 missed cup_7e21" was
            true only because BM25 never searched descriptions. Fix: a `text` sub-field under the
            same field. Current ES allows it; copy_to FROM semantic_text is still impossible.

## h06 · web · `/` is now hero + dashboard on one scroll; landing calmed, lightened, gated; capture page one click away
Files:      web/landing/{index.html,scene.js,layout.js,watchers.js,heads.js,title.js,hall.js,dash.js,dash.css},
            web/landing/{dressing.js,enter.js} (builders), web/LANDING-TASK.md
Verified:   headless Chrome 2560x1440, six modules: 60 fps, 815 draw calls (was 972 with two fewer modules),
            0 console errors. Motion audit, 31 rigged chains, 20 s under a fast real pointer: worst joint
            step 7.1°/frame (was 16.9), watcher head speed 3-4 u/s (was ~7), 0 NaN.
            Scroll to #dashboard → scene clock advances 0.00 s per 1.5 s (loop stopped), resumes on return.
            Browser Sentry: SDK loaded, client + replay active; the ONLY external request is Sentry ingest.
            Status panel lists recent captures; cap_0004 shows "REJECTED — why?" → /capture/cap_0004.
Blocked on: Elasticsearch credentials (both empty in .env) — /api/search answers 503 elastic_unconfigured;
            two kNN cut-off thresholds in dash_api.py are uncalibrated until real scores exist.
Surprise:   (1) The lag was LIGHTS, not geometry: the hall's 4 SpotLights were evaluated per fragment by
            every lit material on the page (~900 draw calls of arms) to tint a patch of back wall.
            (2) `antialias: true` bought nothing: the scene is drawn to the post pass's own target and
            reaches the canvas as one full-screen quad, so MSAA multisampled a rectangle.
            (3) "Arms hanging in mid-air" = an 18-segment cap: long necks came up short, and a
            reach clamp then dragged their ROOT into the frame. Long reaches now get thicker necks.
            (4) The title letters "collided" because their swing springs had damping ratio 0.12-0.16:
            one knock rang for ~8 s, and a twirl gag swept a letter 40° through its neighbours.
            (5) elastic-09: the text_similarity_reranker scores only the FIRST value of a multi-valued
            field, so rerank_position sees one of an object's three descriptions (retrieval sees all).

## h06 · web · /capture/<capture_id> — "why was this diff wrong?" on one screen
Files:      web/capture_api.py, web/store.py, web/pages/capture.html, web/pages/capture.js, web/pages/pages.css
Verified:   live on :8000 via server.py's router hook — `GET /capture/cap_0004` → 200, `GET /api/capture/cap_0004` →
            gate pass=false failing=[tilt_rate_max 0.0825 > 0.05], telemetry 201 samples/signal, spike 0.134 rad/s at
            -200 ms, nav.retry=cap_0005, 9 phantom moves of 10; `/api/capture/cap_9999` → 404 {error,detail,retryable};
            `/api/capture/CAP-1;drop` → 422. By hand from demo.ndjson: mug_a1b2 in cap_0003 x 0.2489/0.2497/0.3090 →
            60.1 mm spread = API's 60.1 (y 28.7, z 9.1). Headless Chrome 1440x900 + 390x844: 0 console errors, no
            horizontal scroll, 0 external requests; hover crosshair reads real samples ("-340 ms | tilt_rate 0.0027");
            retry link lands on cap_0005 → PASSED. Mock-ES run: source=elasticsearch builds the real Sentry URL from
            SENTRY_ORG_SLUG + sentry_trace_id; a `javascript:` sentry_url on a doc is ignored; a genuine ES query error
            propagates instead of silently falling back to fixtures.
Blocked on: Elasticsearch credentials (page runs on fake/out/demo.ndjson, tagged "fixture data"); camera frames —
            mappings are dynamic:strict and have no frame_uri, so the three views are labelled placeholders;
            "Open in Sentry" is disabled for fixture captures (synthetic trace ids) and live only for real documents.
            server.py must be restarted once to load the final store.py (the running process imported an earlier one).
Surprise:   "Spread beyond the quantum" cannot flag a bad diff: in EVERY capture, passing ones included, every object's
            cameras disagree by 28-64 mm against a 10 mm quantum. What separates the rejected capture is the comparison
            the page now makes per object — how far it was read to have moved vs how far apart its cameras put it, and
            vs the retry: 9 of cap_0004's 10 "moves" were 22-45 mm, smaller than the cameras' own disagreement, and the
            retry five seconds later found only the mug's real 214 mm move. The robot moved, not the room.

## h00 · observability · Sentry plan: tracing works now; trial ends 2026-10-02
Files:      —
Verified:   Sentry billing API → org na-alh on `am3_t` (Trial), trialEnd 2026-10-02; spans,
            profiles, logs, errors all `accepted` in stats_v2; story trace 50c0ccf2 has 7 spans
Blocked on: DANIEL — na-alh was made with a personal email. Education plan (5M spans/mo) needs
            GitHub Student Pack first, then sentry.io/for/education on @uwaterloo.ca. Upgrade or
            recreate the org; DSNs NOT re-pointed (reissuing means three machines).
Surprise:   Nothing is plan-gated today. The trial outlives the hackathon, so the .edu move is
            about keeping Sentry after Oct 2, not about this weekend. An empty Performance tab
            is AM3's data model (transactions live in spans → Explore → Traces), not gating.

## h00 · cloud · telemetry: Pi tap + 10 s ring + 100 ms batches; laptop hub with 4 isolated sinks
Files:      robot/telemetry.py, telemetry/hub.py, telemetry/test_telemetry.py,
            docs/23-telemetry.md (§8 as built), obs.py (robot_failure breadcrumb fix)
Verified:   `pytest telemetry` → 13 passed (×3 runs): ring fills with no consumer; 5 samples ×
            10 msg/s; replay↔live seam has no gap/dup; hung + throwing sinks stall nobody;
            1 s mid-stream outage → zero samples lost. Live: fake Pi + hub as processes →
            GITSPACE-6 `fell_over`, 34 breadcrumbs spanning 1.98 s, tagged fw/boot_id;
            SIGTERM → queue + buffer spooled (8360 docs on disk vs 6920 at last stats);
            fake Pi → hub → web's POST /api/internal/event → a curl on /api/events got
            7 `event: telemetry` frames in 4 s, in web/events.py's shape
Blocked on: WEB — push_event isn't _untrace()d, so 2 Hz telemetry = ~7200 Sentry transactions/h.
            ES — ELASTIC_URL and ELASTIC_API_KEY are now EMPTY in .env (changed 21:15), so the
            ES sink spools to disk; it backfills on its own once they're set.
            robot/server.py doesn't exist: the real balance-loop source + /stream route go there
            (5-line FastAPI snippet in the robot/telemetry.py docstring).
Surprise:   obs.robot_failure() put breadcrumbs on the long-lived scope, so fall #2 arrived
            carrying fall #1's tilt graph (93 breadcrumbs spanning 25 s). Now scoped per event.
            And replayed samples only dedupe in the TSDS if their @timestamp rounds identically
            to the live copy — which silently fails ~never, unless the boot's clock pairing
            lands near a half-millisecond, where it fails for EVERY sample. Pairing now
            quantised to 1 ms, so every sample is an exact integer ms.

## h00 · elastic · story_demo.py really indexes the Sentry join now + SETUP-CHECKLIST.md
Files:      scripts/story_demo.py, elastic/ingest.py, elastic/SETUP-CHECKLIST.md, elastic/NOTES.md
Verified:   story_demo.py run from a scratch copy against an in-memory ES stand-in (real client,
            bulk/msearch swapped), with Sentry pointed at a dead local DSN:
            live → "joined: both lookups return exactly the 4 documents written", exit 0.
            ES loses one doc → exit 1 "read back {…2} … but wrote {…3}". Bulk rejection → exit 1.
            No cluster (today) → exit 1 "ELASTICSEARCH HALF NOT DONE: ELASTIC_URL and
            ELASTIC_API_KEY must be set". story_docs.json = 4 bulk actions, 0 with _index in _source.
            NOT live. ELASTIC_URL still empty.
Blocked on: ELASTIC_URL + ELASTIC_API_KEY; then elastic/SETUP-CHECKLIST.md is one paste.
Surprise:   Corrects the h00 observability entry: its "4 ES docs" were a file, never indexed, and
            would have been rejected (_index inside _source). Second: obs.trace_fields() returns a
            trace id even when Sentry is NOT initialised, because the SDK mints spans regardless.
            With SENTRY_DSN empty, every doc would carry a link to a trace Sentry never received.
            story_demo.py now strips them and fails loudly. obs.py needs a one-line is_active() check
            (see elastic/NOTES.md).

## h00 · perception/segment · describe.py LIVE on gpt-5: one description per instance per camera, unreconciled
Files:      perception/describe.py (max_output_tokens 2000, truncation surfaced as an error, token usage
            on the perception.describe span), perception/tests/test_describe.py
Verified:   Real chain: YOLO → lift → gpt-5 → merge → observations(). Input: 3 perspective-warped
            "cameras" of COCO val2017 #397133 (a kitchen table), since there are no captures yet.
            35 views in 8.8 s (8 workers), 0 failed, 13,020 in / 2,057 out / 0 reasoning tokens at
            effort=minimal. merge → 20 objects, 2 seen by all 3 cameras, every view's words kept, e.g.
            one bowl: "tan oval ceramic bowl" / "light tan wooden oval bowl" / "wooden triangular corner
            unit". `pytest` (my 5 test files) → 47 passed.
Blocked on: real stereo captures (the three views are warps of one photo, not three rigs)
Surprise:   At 480×270 YOLO's class was plainly wrong on about 4 of 13 multi-view objects where the
            VLM views agreed with each other: "oven" → "metal mugs", "bowl" → "pizza",
            "spoon" → "brown bottle". associate.first_sight_class prefers YOLO's label, and class is set
            once and never changes, so a mug would live in oven_xxxx.yaml forever. Also, "dining
            table" / "oven" / "refrigerator" become objects; nothing yet says furniture is a zone, not
            an object. And gpt-5 at effort=minimal used 0 reasoning tokens here, but the 2000 cap
            stays: at a tight cap the reply comes back valid and EMPTY.

## h06 · web · the git graph as a control surface: /api/graph, /api/diff, /api/command + the History section
Files:      web/graph_api.py, web/landing/graph.js, web/landing/graph.css
Verified:   9 pytest tests against the REAL room.git + fixture enrichment (scratchpad graph/test_graph_api.py):
            nodes == `git log --all --date-order` exactly, time-ordered; "the bench, tidied" has two children
            (main + movie-night) and the layout puts movie-night in lane 1 (node x = [20,46,20,20]); HEAD's
            node carries rejected_before [cap_0004 · tilt_rate_max 0.0825, would have moved 10] and links to
            /capture/cap_0004; with enrichment forced to fail the graph still renders from git alone;
            /api/diff's mug delta equals the distance computed by hand from the two YAML poses; bad sha → 422,
            unknown sha → 404, cherry-pick → 403, `--upload-pack=x` as a ref → 422, all in the §2.7 shape;
            room.git HEAD / refs / `status --porcelain` identical before and after.
            Headless Chrome 1440x900 + 390x844: a REAL double-click on a node sends 0 commands (the run
            control is disabled for 600 ms and ignores event.detail > 1), an early click inside the arming
            window sends 0, the run control never takes focus, one deliberate click → 202. No console errors,
            no horizontal scroll. Live :8000 restarted: "router graph_api: loaded (3 routes)".
Blocked on: NO ROBOT MOVES YET. roomctl/executor.py does not exist, so POST /api/command validates, plans the
            ops (3 ops, ~84 s for "revert to the tidied bench"), answers 202 with executor:"not_connected",
            publishes a `job` event saying so, and never writes to room.git. The UI says the same in words.
            Rerun ghost preview (B4 step 4) is not wired either: the preview is the object-level diff.
Surprise:   `quality_ok` on a commit node can never be false in a system whose gate works — a rejected capture
            is never committed — so enriching by commit_sha alone draws an all-green graph and hides the one
            interesting thing. The rejected capture has to be hung on the NEXT commit on its branch (by
            room-events time): "cap_0004 was thrown away before this commit". Second: an object that changes
            ZONE changes PATH (zones/<zone>/<id>.yaml), so git reports a delete plus an add for one physical
            move; unpaired, the planner would have asked the arm to take speaker_6b12 away and then put back
            an object "not in the room". /api/diff pairs them: moved 57 cm, shelf → desk.

## h00 · perception/pointcloud · traversal (docs/24 Part A): costmap.py collision band + raycast.py line of sight
Files:      perception/costmap.py, perception/raycast.py, perception/voxelize.py (the grid only),
            perception/tests/test_costmap.py, test_raycast.py, test_voxelize.py
Verified:   `python3 -m pytest perception/tests` → 121 passed. Pedestal table, 0.40 m overhanging top:
            the pedestal blocks, the top doesn't; solve_base_pose stands the robot UNDER the top with the
            mug inside the reach annulus and in view. Same room at docs/24's robot_h = 1.0 → None, all 180
            candidates rejected at "base_fits" (the costmap), 0 at IK — in the log and on the span.
            DDA matches brute-force slab intersection on 1000 random rays. Viewpoint: > 45° round, clear
            view. occlusion_check: blocked from every camera → unobserved; one clear view → removed.
            13 deliberate mutations, all caught. 50k-point room: voxelize 8 ms, costmap 1 ms, base pose
            9 ms, viewpoint 7 ms — inside the ~100 ms planning tier.
Blocked on: measured ROBOT_H and camera height; SO-101 reach annulus + reachable() from robot/arm.py.
Surprise:   docs/24's own default (robot_h = 1.0 m) puts a 0.75 m table top INSIDE the body band: its
            rule only holds if ROBOT_H is below table height. And the octree cube starts at exactly
            z = 0, so each floor voxel keeps only the upper half of the floor's noise — with 1 cm of
            noise its top points clear 2 cm routinely, and inflated speckle walls off open floor
            (13 cells → ~3 m² blocked). Median height + dropping lone low voxels fixes σ ≤ 1 cm; at
            2 cm, Z_FLOOR must rise to ~3σ.

## h00 · perception/segment · both acceptance tests run with traces; ES re-id query fixed against the live cluster
Files:      perception/associate.py, perception/tests/test_segment.py, perception/tests/test_associate.py
Verified:   `pytest -s -v` on the two acceptance tests → both PASSED. Touching: DBSCAN on 643 mug+book
            voxels → 1 cluster (100%); masks → book centroid (-0.025, 0.025, 0.800), cup (0.086, 0.041,
            0.766). Returning: cup_0162 added (c0) → removed (c1) → absent (c2, c3) → returned (c4) with
            first_seen 2026-09-19T14:00:00Z. The stapler dropped at its old spot → new id
            stapler_5fb4. Without history the mug gets a new id. All 58 tests pass (mine + raycast).
Blocked on: room-objects has 0 docs, so the live query returns nothing yet. The returning-object
            test still uses a word-overlap stand-in for ES.
Surprise:   The first LIVE run of ESHistory's query (serverless 9.6) was rejected with a 400. rrf
            defaults rank_window_size to 10, and ES requires rrf's window ≥ the reranker's (50) ≥ size
            (20). The fake-client test had checked the query's shape and passed. Fixed: both windows
            = max(50, size), and the test now asserts that ordering.

## h00 · cloud · Elastic is on GCP us-east4: web tier stays AWS us-east-1, now cross-cloud
Files:      docs/19-deployment.md (diagram, region row, new cross-cloud section, what-lives-where),
            .env.example (ELASTIC_URL placeholder → *.es.us-east4.gcp.elastic.cloud), DIAGRAM-DRIFT.md
Verified:   diagram columns checked programmatically (connectors at col 11/50, boxes 71/78 wide);
            `grep` for aws.elastic / us-east-1 across *.md, *.html, .env* → only elastic/'s own
            SETUP-CHECKLIST.md still shows the AWS URL shape (theirs to change)
Blocked on: nothing. The launch-day latency number is a one-liner in docs/19 — run it on the instance.
Surprise:   Cross-cloud sounds like an architecture change and mostly isn't: us-east4 (Ashburn)
            and us-east-1 (N. Virginia) are one metro, and Serverless was always a public HTTPS
            endpoint anyway. The real new constraint is that the web tier's region is no longer
            free to choose — a west-coast AWS region would add ~60–70 ms to every ES round trip.
            Moving web to GCP instead would put long-lived AWS keys on the box for SQS/S3.

## h00 · elastic · LIVE: 19/19 query tests pass on the cluster, story_demo joins for real
Files:      elastic/queries.py, elastic/setup_elastic.py, elastic/tests/test_mappings.py (new, 64 offline
            invariants), elastic/NOTES.md, elastic/SETUP-CHECKLIST.md
Verified:   `setup_elastic.py` 2nd + 3rd live run: every line exists/in sync, exit 0 (acceptance).
            `pytest tests -W error::ElasticsearchWarning` → 83 passed (19 against serverless 9.6.0;
            test- indices deleted after). `ingest.py ../story_docs.json` → the h00 docs (trace
            50c0ccf2…) now read back 3+1 by trace id and by cap_78072. `scripts/story_demo.py` →
            trace e6d4e9e5…, issue grasp_slipped, "joined: both lookups return exactly the 4
            documents written", exit 0.
Blocked on: nothing. fake/out/demo.ndjson is not in the real indices yet (checklist line 4).
Surprise:   Every query passed on first contact. The live bugs were elsewhere. (1) ES|QL without a
            LIMIT silently truncates at 1000 rows: 6 queries had none, fixed and tests now fail on that
            warning. (2) A JINA_API_KEY in .env silently flipped setup from EIS to jinaai (drift warning
            on run 2). EIS is now the explicit default. Verified live: semantic_text in a TSDS, the
            raw_description.text sub-field, and the downsampling lifecycle are all accepted.

## h00 · cloud · telemetry lands in the LIVE robot-telemetry TSDS, gapless across a Pi disconnect
Files:      — (robot/telemetry.py + telemetry/hub.py unchanged; `pytest telemetry` 13 passed)
Verified:   fake Pi → hub → ES sink → live cluster (Serverless 9.6.0, GCP us-east4), with a
            1.5 s Pi disconnect mid-run: written 8120, errors 0, gaps 0; 240-doc shutdown tail
            spooled then backfilled → 8360 docs, 8 signals × 1045. By query: pitch has 1045
            distinct @timestamps over 20.88 s = exactly a gapless 50 Hz series. Re-sent 400
            written docs → 400 × 409, 0 written: TSDS dedup makes replay idempotent, live.
            ES|QL `STATS MAX(ABS(value))` on tilt_rate works (2.84 rad/s).
            Sentry: 782 traces / 2148 spans in 3 h are queryable in the spans dataset behind
            Explore → Traces (verified by API, not by eye).
Blocked on: nothing. The data is SYNTHETIC (FakeRobot — no balance loop until robot/server.py):
            window 2026-09-19T01:43:34.793Z → 01:43:55.673Z. 7 d retention expires it; to drop
            it sooner: POST robot-telemetry/_delete_by_query {"query":{"range":{"@timestamp":
            {"gte":"2026-09-19T01:43:34Z","lte":"2026-09-19T01:43:56Z"}}}}
Surprise:   A 400-doc _bulk from the laptop to us-east4 takes 425 ms median, 811 ms max — ~40%
            of every second. The ES sink's own thread is what keeps that off the hub's event loop.
            And Explore → Traces is ~10:1 noise: every static asset the web server serves is its
            own transaction (~20 per page load), burying the robot traces. Wants a traces_sampler
            in obs.init that drops static files + /api/internal/event — a SENTRY_STORY entry
            once it lands and the before/after is measured.

## h07 · web · pointed at the LIVE cluster: real retriever accepted, /capture renders a real doc with a working Sentry link
Files:      web/store.py (per-axis disagreement), web/server.py restarted on the live credentials
Verified:   `curl :8000/api/health` → {"ok":true,"elastic":{"version":"9.6.0","flavor":"serverless"}}.
            `GET /api/search?q=mug` → retriever "text_similarity_reranker(rrf(bm25, semantic))", reranked: true,
            accepted by the cluster on the first rung (no fallback), bm25_fields discovered from the live
            mapping = ["class","raw_description.text"], 416 ms for the fused query + both solo legs + HEAD.
            0 results: room-objects is still empty (master is loading fake/out/demo.ndjson).
            `GET /api/capture/cap_82093` → source "elasticsearch", gate REJECT on tilt_rate_max 0.239,
            sentry.url https://na-alh.sentry.io/performance/trace/e6d4e9e5… — "Open in Sentry" is ENABLED
            on a real document; disagreement 49.0 mm on x, outlier cam2, 4.9x the 10 mm quantum.
Blocked on: the demo data load — until it lands /capture/cap_0004 is a 404, the graph's "rejected before
            this" chip and the status panel's recent-captures strip are empty (web reads ES first and
            will not paper over a reachable-but-empty cluster with fixtures). A watcher runs the search
            acceptance check (cup_7e21: vector ✓, bm25 ✗) the moment room-objects has documents.
Surprise:   The capture page said "no object was seen by two cameras, nothing to compare" on the first
            REAL document, while showing x = 0.421 / 0.418 / 0.467 right above it: the disagreement
            code demanded raw_x AND raw_y AND raw_z, and the real docs carry raw_x only. Fixture data had
            all three axes, so every test passed. Spreads are now per axis, over whichever cameras
            reported that axis. Also: a server started before .env changed kept the old (rejected) API
            key — /api/health said elastic_auth while a fresh process was fine. Restart after .env edits.

## h00 · perception/segment · re-id LIVE: ES ranks the right object, but can't tell a return from a newcomer
Files:      perception/associate.py (colour veto; reranker score = rank only), perception/tests/test_associate.py
Verified:   Live serverless 9.6, throwaway `test-perception-room-objects` built from the real mapping
            and deleted after (room-objects itself has 0 docs). 13 kitchen objects indexed with one
            camera's real gpt-5 words, queried with the other cameras' words: right object top-1 in
            10/13 (5 lookalike bowls included). The 3 misses are views where gpt-5 described different
            things (pan vs ladle, bottle vs "too blurry"). associate() end to end: book unchanged, mug
            `returned` with its original id, stapler `added`. `pytest` (my 5 files) → 48 passed.
Blocked on: real multi-view captures to calibrate a visual threshold
Surprise:   The reranker score can't decide `returned`. Right matches scored 1.03–1.90, the best WRONG
            one 1.07–1.92, and top1−top2 margins overlap too (0.015–0.553 vs 0.022–0.443). The old 0.5
            threshold passed every newcomer: 13/13 would have taken an existing id. Colour (CIELAB ΔE)
            is a useful veto (same object ≤ 14.1 across ±20% exposure, different objects median 29) but
            the dark objects overlap. .jina-clip-v2 image embeddings on EIS: 1024-d, 0.36 s, max 16
            inputs per request. 10/13 objects are closer to their own other view than to any other
            object (text: no separation). Still no single threshold (same 0.78–0.98, different up to
            0.93), and that's the easy case: the views are warps of one photo.

## h07 · web · `/object/<id>` — the full life of one thing, with the "where did it go?" verdict
Files:      web/object_api.py, web/pages/object.html, web/pages/object.js, web/pages/pages.css (appended),
            web/server.py (one name added to the router tuple)
Verified:   `pytest scratchpad/obj/test_object_api.py` → 5 passed, against the fixture file + the REAL room.git:
            tool_4f2a → ABSENT, last seen in the first commit, gone after b3691ea "the bench, tidied" (cap_0002),
            verdict left_room: cam0/cam1/cam2 each saw mug_a1b2 unoccluded 16–19 cm from the spot in cap_0002,
            watch loop saw the spot 16/16 times and the hammer 0; mug_a1b2 → PRESENT, moved 0.19 m (by hand:
            (0.42,0.18)→(0.61,0.18)), 0.22 m on movie-night; bowl_0c55 → "exists on another branch";
            unknown id → 404 §2.7 shape, malformed → 422; POST …/point → 202 executor:"not_connected",
            room.git HEAD and `status --porcelain` unchanged. Headless Chrome 1440x900 + 390x844: no console
            errors, no horizontal scroll. Real puppeteer double-click on the preview control → 0 POSTs, click on
            the still-disarmed run control → 0, one armed click → exactly one 202.
            :8000 restarted once: `/` `/api/status` `/api/graph` `/capture/cap_0004` `/object/tool_4f2a` → 200.
Blocked on: DATA. Elasticsearch became reachable at ~21:39 and holds almost nothing (room-objects 0,
            room-events 0, room-clouds 2 smoke docs, room-observations 6) — so on the LIVE server
            /api/object-life/* AND /api/capture/cap_0004 now answer 404: the pages read ES first and fall
            back to fake/out/demo.ndjson only when ES is UNAVAILABLE. Someone must bulk-load demo.ndjson.
            No robot moves: roomctl/executor.py does not exist; `point` is not in WEB_ALLOWED_COMMANDS.
Surprise:   Fixing the Elasticsearch credentials BROKE the demo pages. "ES first, fixtures as fallback" is
            only safe while ES is down: the moment it answers, an empty cluster is the truth and the capture
            page — the prize page — 404s. The object page now says so in its 404 ("reachable but room-objects
            is EMPTY there") instead of "no such object". Second: an object's track must follow commit
            ANCESTRY, not the clock — drawn in time order the mug "moved" from its movie-night position to
            its main position, a journey that never happened.

## h00 · perception/pointcloud · voxelize.py: occupancy grid + octree keys, indexed to room-voxels (live cluster)
Files:      perception/voxelize.py, perception/tests/test_voxelize.py, perception/tests/test_voxelize_live.py
Verified:   `.venv/bin/python -m pytest perception/tests/test_voxelize_live.py` → 5 passed ON THE REAL
            CLUSTER: a synthetic room indexed under a throwaway `selftest-*` sha, read back with
            independent queries — count = voxels, EVERY doc has the span's sentry_trace_id, _id is
            `<sha>:<voxel_key>`, terms on voxel_key_l3 match local counts, a `shape` envelope on `cell`
            (cartesian point, F_world metres) returns exactly the voxels on the table, zone=desk and
            object_id counts match — then deleted; room-voxels is back to 0 docs. Unit: 15 passed —
            docs/20's "a point in cell 370 keys as 370…", digit bits are x<<2|y<<1|z, keys_of() agrees
            with docs/11's encoder on 2500 points incl. exact cell boundaries, docs fit the strict
            mapping. `python3 -m pytest perception/tests` → 133 passed, 5 skipped (live, no ES client).
Blocked on: nothing. Real voxels need a real scan + commit sha: index_voxels() is the call.
Surprise:   The cube is pinned in two places — .env (ROOM_*/OCTREE_LEVELS) and room.git/room.yaml — and
            roomctl reads only os.environ with built-in defaults, so a process that never loaded .env
            silently uses the defaults. They happen to match today. voxelize reads .env itself and
            index_voxels() refuses to write when the cube disagrees with room.yaml.
            Also: my first live run left 1,578 selftest docs behind — the fixture failed after
            indexing and before its cleanup. Deleted by sha prefix; cleanup is now try/finally.

## h00 · elastic · acceptance met on the REAL indices (fake room loaded)
Files:      elastic/SETUP-CHECKLIST.md, elastic/NOTES.md (status only)
Verified:   after the fake's owner's `scene_gen.py --index fake/out/demo.ndjson` (8,855 docs, 0 rejected),
            read-only against room-*: counts objects 45 · voxels 3,004 · observations 2,787 ·
            clouds 7 · events 5 · telemetry 11,375. queries.search_objects("mug") →
            [mug_a1b2, cup_7e21, …]: cup_7e21 is #2, and "mug" appears nowhere in its docs.
            lexical_only("mug") → [mug_a1b2]. commit_at(now) → 1a668ec0… (ES|QL wall-clock → sha).
            All three elastic/ acceptance criteria that don't need the robot: done.
Blocked on: nothing. Open: telemetry sustaining ~400 docs/s (needs the live Pi stream).
Surprise:   robot-telemetry already holds 11,375 docs against the fake room's 3,015, so another
            writer is live on it. dynamic:strict guarantees the docs that landed match the mapping;
            anything that didn't was rejected on that writer's side, so check its bulk errors.

## h06 · integration · fake/scene_gen.py: a fake room that lies the way cameras lie
Files:      fake/scene_gen.py, fake/scenes/*.yaml (5 scenes), fake/README.md (the ES doc contract)
Verified:   `scene_gen.py --demo --reset` → 4 commits on 2 branches + 1 gate-rejected capture,
            8,855 docs; every doc validated against elastic/'s dynamic:strict mappings, 0 TSDS
            identity collisions; `--index fake/out/demo.ndjson` into the live cluster → 8,855
            indexed, 0 failed; live BM25 "mug" → [mug_a1b2], semantic → cup_7e21 at #2
Blocked on: nothing
Surprise:   The acceptance object was contaminated by its own commit. Copying the commit
            message onto every room-objects doc meant "afternoon: mug moved" made the
            ceramic cup a BM25 hit for "mug" — the one object built to prove BM25 misses it.
            Commit-level text now lives only on room-events (elastic caught it).

## h06 · integration · roomctl/state.py: the object record, frozen (then amended once)
Files:      roomctl/state.py, tests/test_state.py (golden bytes), docs/04 + docs/20 updated
Verified:   `pytest tests/test_state.py` → 19 passed
Blocked on: nothing
Surprise:   docs/04's own example object file would have been a phantom diff on every scan:
            it committed `confidence` and `observed_by`, which change between two scans of
            an untouched room. And yaw had to become an AXIS [0,180) an hour after the
            freeze — perception's PCA can't tell front from back, so a [0,360) heading would
            flip 180° between scans with nothing moving.

## h06 · integration · roomctl/repo.py + cli.py: `room init/status/diff/add/commit/log`
Files:      roomctl/repo.py, roomctl/cli.py, roomctl/__main__.py, tests/test_cli.py
Verified:   `room status --scene messy_bench` → modified mug (moved 0.19 m, turned 15°→40°),
            deleted marker, untracked scissors; `room diff` byte-identical to `git diff` (test)
Blocked on: nothing
Surprise:   /usr/bin/git on macOS is an xcrun shim: 29 ms per call vs 13.5 ms for the real
            binary. `room status` makes several; resolving the real git once halved it.

## h06 · integration · tests/test_idempotent_scan.py — gate G2, green on the fake
Files:      tests/test_idempotent_scan.py, tests/README.md
Verified:   `pytest tests/test_idempotent_scan.py -v` → 12 passed, 8 skipped in 7.8 s
            (4 scenes × 2 seeds × 20 rescans clean; occluded ≠ deleted; 1 move = 1 file;
            8 mm nudge = no diff; the 8 skips are the real-perception rows)
Blocked on: real-data rows need perception/pipeline.py `scan_into()` + recordings — no owner (D9)
Surprise:   A green gate proves nothing unless it can go red. Added a control that strips
            hysteresis: the same rescans go dirty within a few scans, because several
            objects sit exactly on a 1 cm bucket boundary (z = 0.755).

## h06 · integration · roomctl/executor.py: diff → ordered pick-and-place → verify by rescan
Files:      roomctl/executor.py, tests/test_executor_order.py, roomctl/cli.py (reset/checkout/revert)
Verified:   300 random layouts replayed step by step — never a place onto an occupied spot;
            `room checkout swap` → stage cup, move mug, unstage cup → rescan clean;
            `room reset --hard --scene messy_bench` → "cannot apply hunk: object
            'marker_c3d4' not present in room", "2 of 3 objects put right", exit 1
Blocked on: real robot client (MockRobot prints and moves the fake room)
Surprise:   Gripper clearance must not apply to the target layout. With 1.5 cm clearance on
            every footprint, a mug committed touching a book could never be restored — the
            planner refused the committed state itself. Clearance now only guards spots the
            planner picks (staging); placement tolerates < 1 quantum of overlap.

## h08 · web · GAP 3 closed: web runs elastic/queries.py; parked-key handling; wiring audit recorded
Files:      web/es_shared.py (new), web/dash_api.py (-75 lines of retriever building, now an adapter),
            web/tests/test_search_adapter.py (new), web/server.py (usable()/parked(), guarded obs.init,
            elastic_paused, browser DSN off while parked), web/store.py, web/landing/sentry.js,
            web/requirements.txt, docs/10-open-questions.md (GAP 3 resolved, GAP 4, GAP 5, edges)
Verified:   BEFORE the pause, live: `/api/search?q=mug` → mug_a1b2 (bm25+vector) #1, cup_7e21 (vector only)
            #2, nothing else; "keys" → keys only; "xylophone" → no results. Vector cut calibrated on live
            scores (noise floor 0.556 → floor 0.62, 0.85 x top).
            AFTER the pause, offline only: `../.venv/bin/python -m pytest web/tests -q` → 6 passed (a fake
            client handed to the REAL shared Queries; asserts the fused legs are elastic/'s `_lexical` /
            `_semantic`, exactly 3 calls, cup = vector-only, kNN neighbours dropped, HEAD from git).
            Paused server: /api/health → elastic_paused, /api/config → dsn null + paused true, every page
            200 from fixtures, `/api/search` → 503 elastic_paused, 0 outbound ES calls in its log.
Blocked on: the adapter against the live cluster is unverified until 01:00 (elastic-09's live tests for
            semantic_only + capture_id run then too). GAP 5 (store.py onto Queries) not started.
Surprise:   Parking a key as `KEY=# parked…` does not unset it: python-dotenv makes the comment the VALUE.
            obs.init() then hands sentry_sdk a non-empty garbage DSN and it RAISES BadDsn — any process
            that calls obs.init() at import dies on its next restart (web would have). And the garbage
            ES key is non-empty, so `if key:` says "configured" and it is sent to the real cluster.
            Second one: the server started BEFORE parking kept the live keys in memory and kept calling;
            parking only takes effect on restart. Third: SENTRY_DSN_WEB was not parked, and every
            headless capture of the page had been a billed Session Replay — the browser SDK now stays
            off under automation (navigator.webdriver) and whenever the server DSN is parked.

## h00 · perception/pointcloud · connections, while every external API is parked: nothing hit OpenAI/ES/Sentry
Files:      perception/fuse.py (odom_to_world, world_to_odom, robot_pose, instances_to_world),
            perception/voxelize.py (retries, spool + flush_spool, parked-key short circuit, stage/index_staged,
            zone_of), perception/depth.py (CLI survives a parked DSN), perception/tests/test_voxelize_es.py,
            test_fuse.py, test_trace.py (now a recording fake — no sentry_sdk.init), test_voxelize_live.py
            (opt-in GITSPACE_LIVE=1, never while parked); docs/10-open-questions.md P1–P12
Verified:   `python3 -m pytest perception/tests` → 177 passed, 8 skipped; `.venv` (real elasticsearch exception
            classes, still no network) → 103 passed. ES client tested at the boundary with a fake: flaky
            connection retried at 0.5/1.0 s, per-doc 429s resent alone, 401 spools at once, 503 spools after
            3 tries, a 400 rejection raises, a parked key spools without importing the client, flush replays
            oldest-first and keeps what fails. 7 client mutations, all caught. BB's odometry update maths vs
            odom_to_world: a left arc is +y and CCW.
Blocked on: master's GAP 1 hook calling voxelize.index_staged(); zone decision (P8); pipeline owner (D9).
Surprise:   Parking a key doesn't make code skip it. `SENTRY_DSN=# parked…` is non-empty, so obs.init()
            calls sentry_sdk.init and it RAISES BadDsn — the depth CLI (and telemetry hub, story_demo) would
            die at startup. And setup_elastic.connect() still sends the parked ES key to the cluster for a
            401. Only web/server.py knew the rule. Also: segment's instances were in each camera's F_rect
            and nothing moved them to F_world before merge — six modules, zero calls between two of them.

## h00 · elastic · wiring while APIs are parked: to_es_doc (GAP 2), commit_actions (GAP 1), audit
Files:      elastic/records.py (new), elastic/ingest.py, elastic/setup_elastic.py, elastic/queries.py,
            elastic/tests/{test_records,test_ingest,test_connect,test_query_shapes}.py (new),
            docs/10-open-questions.md (GAP 1/2/4 status + GAP 6-11 + web + docs drift)
Verified:   offline only, no API touched: `pytest tests --ignore=tests/test_queries.py` → 112 passed.
            to_es_doc key set == mappings/room-objects.json; commit_event == room-events mapping.
            connect() with the real parked .env → "SetupError: ELASTIC_API_KEY is parked … nothing
            was sent". New live assertions (semantic_only, branch=, capture_id) wait for 01:00.
Blocked on: nothing for elastic. The roomctl -> elastic hook (GAP 1) is master's: 3 lines at cli.py:230.
Surprise:   Parking keys as `KEY=     # PAUSED` doesn't unset them. python-dotenv returns the comment
            text as the value, so obs.init() raises BadDsn instead of no-op'ing, and the ES/OpenAI
            clients would SEND "# PAUSED…" as the key. Also: the hybrid retriever had a THIRD copy
            (perception/associate.py), already drifted (BM25 on class only, no collapse). queries.py
            got branch= so it can be reused instead.

## h00 · perception/segment · connection audit: every API boundary mocked, every seam checked against real code
Files:      perception/keys.py (new), perception/{describe,associate,merge,segment}.py,
            perception/tests/test_boundaries.py (new), docs/10-open-questions.md (P1–P7 fixed + 8 open)
Verified:   `pytest` (my 6 files) → 80 passed, 1 skipped. That one needs elasticsearch-py, which
            isn't in pytest's environment, so the venv ran the same checks on the real classes
            (elasticsearch 9.5, openai 3.16). The real parked .env → describe offline, history
            offline, 0 clients built. No call to OpenAI / Elasticsearch / Sentry.
Blocked on: D9 (who writes pipeline.py), raycast per-object FOV, elastic's re-id query taking
            `exclude` and returning _source
Surprise:   Three seams would have failed silently, none with an error. (1) My observation rows
            dropped null keys, and web/object_api reads s["confidence"] directly: a 500 on
            /object/<id> for every fallback-path object. (2) fuse.rect_to_world's docstring invites
            lifting masks from the WORLD array, where segment's edge filter reads height as range
            and quietly stops filtering. (3) A bare ThreadPoolExecutor starts each worker with an
            empty contextvars context, so all 45 gen_ai.chat spans of a capture would be orphans
            instead of children of perception.describe. Copy the context per task.

## h00 · perception/pointcloud · occlusion can no longer delete an object nobody could see
Files:      perception/raycast.py (Camera, per-object FOV + range), perception/depth.py (half_fov_deg),
            perception/fuse.py (instances_to_world removed; docstring), perception/voxelize.py (trace guard),
            tests: test_raycast.py, test_depth.py, test_voxelize.py, test_fuse.py; docs/10 P1, P13, P14
Verified:   `python3 -m pytest perception/tests` → 184 passed, 1 failed: the segment session's
            test_boundaries occlusion test, which passes bare positions and now must pass Cameras (they've
            been sent the drop-in; I ran their exact scene with it: blocked→unobserved, clear→removed,
            facing away→unobserved).
Blocked on: segment.run needs robot_pose (docs/10 P1).
Surprise:   The segment session found it: with one camera list for all objects, a camera facing AWAY has
            a clear line and no view, so a hidden object was judged REMOVED and its file deleted — a phantom
            delete, the exact failure docs/03 warns about. My "no camera → removed" was the same mistake.

## h00 · cloud · the connections: capture clock, jobs, failure photos, heartbeat — all behind mocks
Files:      telemetry/hub.py (Jobs, JobWatcher, *_mono→*_wall, failure reports, watch-loop beat,
            clock lag monitor), telemetry/frames.py (new), robot/telemetry.py (peak waits/None),
            obs.py (measure() in capture_quality, frame= on robot_failure, name= for D11,
            heartbeat monitor_config), scripts/story_demo.py (D12), scripts/audit_architecture.py
            (D17), scripts/bootstrap_laptop.sh (parked keys), .env.example, docs/23, docs/10 D22–D29
Verified:   `pytest telemetry` 28 passed ×5, every external boundary mocked (fake scope/span/tx
            at sentry_sdk; fake client at client.bulk answering 201/409/400 or raising 401).
            Other sessions' obs-touching tests still pass: 106 total incl. web/tests/
            test_sentry_client, tests/test_robot_client, tests/test_publish, perception depth/
            trace/voxelize. Audit: 17 ok · 2 warn · 0 FAIL. No OpenAI/ES/Sentry call made.
Blocked on: 01:00 for anything live. Owners in docs/10: robot/ (t_capture_mono, peak(wait_s),
            GET /job/{id}), master (robot_client.py:166 double-reports), D9 pipeline (write `ts`
            to room-clouds, FrameCache.put), web (_untrace push_event). D16 (AWS, snapshot.sh) next.
Surprise:   The modules agreed on field names and disagreed on TIME. web joins captures to
            telemetry at ±100 ms around room-clouds.@timestamp, but nothing put the shutter on
            the telemetry clock — capture_begin had no t_capture_mono, so the join would have
            read every spike at processing time, seconds late, with no error anywhere. Second:
            capture_quality's sentry_sdk.set_tag hit the process-wide scope, so after one
            rejected capture every later event said "rejected" — the same leak class as the
            breadcrumbs, and obs.attach() has it too.

## h00 · perception/pointcloud · "nowhere to stand" now says why (docs/10 D20)
Files:      perception/costmap.py (solve_base_pose_why, solve_viewpoint_why), perception/tests/test_costmap.py;
            docs/10 P6 → resolved (master wired voxelize.index_staged into roomctl/publish.py)
Verified:   `python3 -m pytest perception/tests` → 188 passed, 8 skipped; the executor's own tests
            (`tests/test_base_pose.py tests/test_executor_order.py`) → 314 passed — solve_base_pose's
            signature and return are unchanged. The docs/24 A1 table at robot_h = 1.0 now reads
            {base_fits: 180, ik: 0, line_of_sight: 0, path: 0}.
Blocked on: nothing.
Surprise:   Returning a falsy "no pose" object that carries the reasons would have broken the executor
            quietly: it does `got and BasePose(...)`, so the object, not None, would reach route(). A twin
            function that returns (pose, why) kept every existing caller byte-for-byte the same.

## h07 · integration · executor stands somewhere: docs/24 A2 through perception's own solver
Files:      roomctl/executor.py (route, ArmModel), fake/scene_gen.py (scene_cloud), tests/test_base_pose.py
Verified:   `pytest tests/test_base_pose.py` → 6 passed; the fake room goes synthetic cloud →
            voxelize.VoxelGrid → costmap.Costmap in ~0.5 s, ~5–10 ms per solve;
            `room reset --hard --scene messy_bench` → "cannot apply hunk: nowhere to stand to
            pick up 'mug_a1b2' … no base pose passes docs/24 A2's four filters", 0 of 3, exit 1
Blocked on: measured SO-101 reach (robot/arm.py) and a demo-table decision (docs/10 D19)
Surprise:   I wrote solve_base_pose, then found the pointcloud session had already built it —
            better (a true 3-D DDA raycast, Dijkstra) and with the same 0.25 m path tie I'd picked.
            Deleted mine. The real surprise is the answer both give: with placeholder reach and
            docs/24's 0.28 m inflation, docs/06's own hero mug (0.4 m in from the table edge) has
            nowhere to stand. The demo table is too big for the arm, found before hardware.

## h07 · integration · audit — every session against its TASK.md, every seam between them
Files:      docs/10-open-questions.md (D13–D21, GAP 4 corrected), TEAM.md (as-built owners)
Verified:   suites with the network blocked in-process: perception 183 ✓, telemetry 27 ✓,
            elastic 113 ✓ + 19 live-only errors, tests/ 385 ✓; setup_elastic --check valid;
            audit_architecture.py 16 ok · 2 warn · 1 FAIL (a regex matching a comment)
Blocked on: nothing
Surprise:   The real pipeline never indexes a rejected cluster (docs/11's "honest mess") —
            only the fake does, so the capture page's discard pile is fixture-only (D14). And an
            offline audit declared `room diff`/`room revert` missing because `room --help`
            listed only argparse's three subcommands; the verbs were built and tested. The fix
            was one usage line.

## h07 · integration · every commit reaches Elasticsearch (GAP 1) + an HTTP robot client
Files:      roomctl/publish.py, roomctl/robot_client.py, roomctl/cli.py (room publish),
            tests/test_publish.py (14), tests/test_robot_client.py (14)
Verified:   all boundaries mocked; full suite 385 passed with sockets blocked; a parked key
            builds no client and spools to .git/gitspace/spool/<sha>.json
Blocked on: live send after 01:00 (`room publish --flush`); HttpRobot needs the hub's job
            stream in-process (the hub is the one /stream consumer)
Surprise:   The documented hook would have indexed the wrong room: ingest.commit_actions' own
            example read the WORKING tree, which after spatial staging (`room add zones/desk/`)
            holds changes the commit doesn't. Built from repo.records(sha) instead; elastic fixed
            the example.

## h08 · web · `/telemetry` — Seer's band over a black-and-white trust ledger; failure rows with [ask Seer] wired to a stumped-first Sentry client
Files:      web/telemetry_api.py (new), web/sentry_client.py (new), web/tools/verify_seer_autofix.py (new),
            web/pages/telemetry.html + telemetry.js (new), web/pages/pages.css (appended block only),
            web/server.py (one name in the router tuple), web/tests/test_telemetry_board.py +
            test_sentry_client.py (new)
Verified:   `../.venv/bin/python -m pytest tests/test_sentry_client.py tests/test_telemetry_board.py` → 29 passed
            (whole web/tests: 58 passed, 1 failed — test_graph_previews::test_merge_preview_edge_cases, the
            graph session's, still being written). Everything ran against the fixture and httpx.MockTransport:
            NO call to Elasticsearch, Sentry or OpenAI; a `boom` transport proves the client makes zero
            requests while parked. Headless Chrome at 1440×900 and 390×844 (domcontentloaded, never
            networkidle): 5 rows, no console errors, no horizontal overflow, no external requests; 40 frames
            posted through the loopback inlet draw tilt_rate_peak against the 0.05 line and say "1 frame
            crossed the gate"; [ask Seer] on cap_0004 prints "Seer is stumped: Sentry is paused to save
            quota" beside the row; a mocked autofix run renders a verdict marked "unverified API shape".
            /pages/seer/tools/* and ..%2f traversal → 404. ONE restart of :8000 from the repo venv: /,
            /api/health, /api/graph, /capture/cap_0004, /object/tool_4f2a, /telemetry,
            /api/telemetry/board, /api/seer/status all 200.
Blocked on: Sentry being unparked. The autofix endpoint (POST/GET /api/0/issues/<id>/autofix/), events-trace
            and the span entry shape are UNVERIFIED against sentry.io — every response says `verified:false`.
            After 01:00 run `python web/tools/verify_seer_autofix.py` (read-only GETs; refuses while parked)
            before anyone demos a verdict. Every capture in the fixture is synthetic, so no row has a real
            trace link yet; the [open trace] button is disabled with that reason rather than faked.
Surprise:   At 2 Hz the hub's LATEST tilt_rate sample is almost never the knock — a 20 ms bump falls between
            frames and the live line stays flat while the gate rejects the capture. The strip has to draw
            tilt_rate_peak (max |tilt| since the previous frame) and show the latest value only as a number.
            Second one: the client parsed "until 01:00" out of the parking comment, then someone re-parked
            .env without the comment, so the real server now says "Sentry is paused" with no time. It says
            what it can read and never invents the hour.

## h00 · perception/segment · LIVE again: gpt-5 per view, re-id on the cluster, one Sentry gen_ai span per LLM call
Files:      perception/describe.py (Sentry gen_ai fields, no double span), perception/associate.py (joint
            re-id), perception/tests/{test_associate,test_boundaries}.py, docs/10-open-questions.md
Verified:   Real YOLO → 35 real gpt-5 calls (0 failed, every camera's words kept) → merge →
            associate() on the live cluster, in one Sentry transaction:
            A) vs the real room-objects, 13 newcomers → 0 took an existing id.
            B) first sightings in a throwaway index, other cameras' views → 13/13 got their own id
               back (greedy: 11/13).
            Sentry API on trace 17d00553…: perception.scan > segment ×3, describe > vlm_call ×35 >
            gen_ai.responses ×35, es.associate > es.search ×26.
            `pytest` (my 6 files) → 82 passed, 1 skipped.
Blocked on: the agent loop (agent/ is a README), so the target agent trace has nothing to wrap yet
Surprise:   Following "wrap every LLM call in obs.agent_turn" double-counted everything. The Sentry SDK's
            OpenAI integration already traces each Responses call and nested its own gen_ai span
            under ours: 70 LLM calls and 26,040 tokens for 35 calls and 13,020 tokens. Also,
            obs.agent_tool's op (gen_ai.mcp.tool) isn't one Sentry's AI Agents module recognises;
            it keys tool calls on gen_ai.execute_tool.

## h09 · web · everything on the LIVE cluster: search provenance, /capture, obs.init, and the Seer row against the real autofix API
Files:      web/server.py (plain obs.init("web")), web/sentry_client.py (org-scoped Seer paths, seer_setup,
            reuse-before-buy), web/tools/verify_seer_autofix.py, web/pages/telemetry.js,
            web/tests/conftest.py (new), web/tests/test_sentry_client.py, web/tests/test_telemetry_board.py
Verified:   1) /api/search?q=mug, live, through elastic/queries.py: #1 mug_a1b2 bm25 ✓ vector ✓ (0.75);
               #2 cup_7e21 "a chipped ceramic cup with a handle, glazed blue" bm25 ✗ vector ✓ (0.666). The
               card prints "BM25 ✗ / VECTOR ✓ #2 — lexical search alone would have missed this".
            2) /api/captures and /capture/<id> read Elasticsearch. cap_0004: REJECT on tilt_rate_max, 201
               samples per signal, spike 0.134 rad/s at −200 ms, 63.8 mm cam2 spread, diff present.
            3) server.py calls plain obs.init("web"); scripts/audit_architecture.py → 17 ok · 2 warn · 0 FAIL.
            4) Seer, read-only against sentry.io (tools/verify_seer_autofix.py 7741490949):
               GET /issues/<id>/autofix/ → 404, empty body (the path docs/26 assumes);
               GET /organizations/na-alh/issues/<id>/autofix/ → 200 {"autofix": null};
               GET …/autofix/setup/ → 200 autofixEnabled true, hasAutofixQuota true, integration.ok false
               (integration_missing), seerReposLinked false.
               sentry_client now uses the org path, reads setup BEFORE any POST (Seer off / no quota →
               stumped, nothing billed), reads an existing run back instead of buying a second one, and
               sends stopping_point=root_cause. Live preflight: cap_82093 and cap_78072 both resolve to
               GITSPACE-2; cap_0004 has no issue → the board says "Seer is stumped: no Sentry issue is
               tagged with this capture" (one GET, no run). /telemetry on live data: 3 failure rows,
               [open capture] ×3, [open trace] ×2 real Sentry links + 1 disabled with its reason, no
               console errors. web/tests: 43 passed.
Blocked on: ONE press of [ask Seer] on cap_82093 — the POST is the only unverified call and it bills a Seer
            run, so it is Daniel's press, not a script's. SEER_VERIFIED stays False until that run is read.
            The 20 graph tests refuse to start: they require a clean room.git and roomctl's working tree
            is dirty right now (mug_a1b2 modified, scissors_9f3a deleted, 2 untracked). Not web's to touch.
Surprise:   Seer has no GitHub integration and no linked repo, so a run can read the issue and the
            breadcrumbs but not our code — a thin verdict is likely, and the row will say why rather than
            dress it up. Also: the tests could have hit the live cluster the moment the keys came back
            (server.py loads ../.env); tests/conftest.py now blanks every credential before import.

## h00 · agent · one real agent turn = one Sentry waterfall: retrieve → decide → revert → pick
Files:      agent/loop.py, agent/tools.py, agent/tests/test_loop.py (new)
Verified:   TRACE (the screenshot): https://na-alh.sentry.io/performance/trace/71e900c492ba4acab0ca317ac0d6e543/
            One transaction, `invoke_agent gitspace` (op gen_ai.invoke_agent), in start order:
              agent.decide #1 → gen_ai.responses gpt-5                9.4 s
              gen_ai.elastic.tool search_objects → es.search          0.5 s  (real room-objects)
              agent.decide #2 → gen_ai.responses gpt-5               11.9 s  (chose to revert 1a668ec)
              gen_ai.roomctl.tool room_revert → robot.pick arm.pick mug_a1b2 → robot.place
              agent.decide #3 → gen_ai.responses gpt-5                3.6 s  (the answer)
            Real gpt-5, real Elasticsearch, real git revert + executor.plan/execute. The ARM IS
            MOCKED and the revert ran on a scratch clone of room.git; the real repo wasn't touched.
            Answer: "reverted 1a668ec… the robot (simulated) moved the mug back, but couldn't apply
            unrelated hunks for a marker and scissors." `pytest agent/tests` → 3 passed.
Blocked on: a real arm run needs roomctl's HttpRobot + a job stream, and a human at the robot
Surprise:   The staged trace already in Sentry ("room revert HEAD [cap_live01]", 173 ms end to end,
            a 77 ms "gpt-5" call) had both tools under the chat span and arm.pick beside it. A real
            turn nests differently: each gpt-5 decision is 3–12 s, and each tool call is a sibling
            BETWEEN decisions. That's what makes the waterfall read as reasoning then acting.

## h05 · cloud · live: telemetry lands, a failed grasp arrives with its photo, 6 of 6 Sentry products
Files:      obs.py (capture_id propagation, attachment cap 480 px/20 per h, D31 gen_ai schema),
            scripts/uptime_tunnel.sh + sentry_uptime.py + deploy_web.sh (new), SENTRY_STORY.md h05,
            docs/10 D16 D30 D33, docs/18, telemetry/test_wiring.py
Verified:   fake Pi → hub → LIVE: robot-telemetry +4760 docs, 8 × 595, gapless 50 Hz over 11.88 s.
            GITSPACE-8 grasp_slipped: tags fw/capture_id/job_id, 34 breadcrumbs over 1.98 s, ONE
            attachment grasp_slipped.jpg (13 KB, the labelled synthetic frame). watch-loop cron
            monitor upserted itself; missed check-in fired GITSPACE-9 within minutes (then muted
            + resolved — no watch loop exists yet). Uptime #10384065 via tunnel: 200 in 535 ms from
            US East. Explore: 108 traces/661 spans in 1 h. Tests: 151 pass across 8 suites.
Blocked on: AWS account for the real web tier (only local creds: IAM user curve-guard-dev).
            Master: one line for D30 (JobWatcher in the CLI). Unmute watch-loop when the Pi's loop runs.
Surprise:   Sentry could not be searched by capture_id — ever. capture_scope tagged a forked
            scope that transactions started outside it never saw: 0 of 7 spans on h00's own
            story trace. Found by querying Sentry for the id it was supposed to carry; now 3 of 3.
            And the "13,249 attachments/day" quota scare was bytes: 2 attachments, 13 KB total.

## h09 · perception/pointcloud · the full chain, live — and G2 FAILS on it; RealSense source added
Files:      perception/pipeline.py (scan_into: D9), perception/synthetic.py (SYNTHETIC recordings — no camera on this
            machine), perception/depth.py (DOWNSAMPLE 0.75; RealSenseDepth + RealSenseFrame; source-agnostic
            depth_capture), perception/fuse.py + serialize.py (floor_z, n_changed as Sentry measurements),
            perception/tests/test_depth_realsense.py; docs/10 P15–P17
Verified:   Live: 4 scans, each one Sentry transaction (11 stage spans; measurements skew_ms, tilt_rate_max, coverage,
            floor_z, n_changed); baseline commit 551990435d on branch `synthetic-selftest` published through
            roomctl → room-voxels 6803 docs (all with sentry_trace_id), room-objects 3, room-events 1.
            G2: `git diff --exit-code` = 1 after EVERY rescan; `GITSPACE_RECORDINGS=… pytest tests/test_idempotent_scan.py
            -k "perception and clean_bench"` → "PHANTOM DIFF on rescan 1". `python3 -m pytest perception/tests` → 197 passed.
Blocked on: a real RealSense session_* folder (+ per-camera timestamps, intrinsics, extrinsics) to re-run G2 on real depth.
Surprise:   The gate the fake passes 25/25 fails on the first real-code rescan. From one stereo view a narrow object's
            footprint smears along the camera ray — an 8×8 cm block measures 13–21 cm long and its yaw follows the smear
            (20°→50°); 1 px of disparity is ~10 cm at 1.35 m with a 6 cm baseline. No SGBM setting, edge filter or closer
            scan fixed it. The fake was never going to find this: its jitter is sub-quantum by construction.

## h00 · agent · THE SCREENSHOT TRACE, with Sentry's own tool op (supersedes 71e900c4…)
Files:      agent/tools.py (TracedRobot dropped: roomctl's MockRobot/HttpRobot now emit robot.* themselves)
Verified:   https://na-alh.sentry.io/performance/trace/6932ef63090640a8866f9c299986e819/
            gen_ai.invoke_agent "invoke_agent gitspace" 9.7 s, one transaction, top to bottom:
              agent.decide #1 → gen_ai.responses gpt-5
              gen_ai.execute_tool "execute_tool search_objects" [tool.type elastic] → es.search
              agent.decide #2 → gen_ai.responses gpt-5
              gen_ai.execute_tool "execute_tool room_revert" [tool.type roomctl] → robot.pick
                  "arm.pick mug_a1b2" [robot=mock] → robot.place
              agent.decide #3 → gen_ai.responses gpt-5
            Exactly 1 pick + 1 place (the in-between run 6751acfd… had them twice: my wrapper plus
            roomctl's new spans). Real gpt-5 + ES + git + executor; mock arm; scratch clone of room.git.
Blocked on: nothing for the screenshot. A real-arm version needs ROOM_ROBOT=http + a human at the robot
Surprise:   Three teams converged on one span vocabulary in about an hour, and each fix showed up in
            the trace immediately. obs.py switched to gen_ai.execute_tool, roomctl's robots started
            emitting robot.pick/place, and the agent's own wrapper had to go the moment they did,
            or every pick showed twice.

## h00 · perception/segment · RealSense-ready: a capture folder runs segment + describe per camera
Files:      perception/describe.py (describe_capture + CLI), perception/segment.py (erosion scales with
            width), perception/tests/test_boundaries.py
Verified:   `pytest` (my 6 perception files + agent/tests) → 88 passed, 1 skipped. A folder in Sarah's exact
            format (docs/27: <cam>_color.png, _pointcloud.npy METRES, _depth_raw.npy uint16 mm, two cams)
            → depth.RealSenseDepth → segment → describe: 2 instances per camera, centroid at 0.80 m.
            A mm point cloud is refused on load.
Blocked on: a real session_*/capture_NNNN folder on this machine. Sarah's repo is code only.
            Then: `python perception/describe.py <capture_dir>`
Surprise:   depth.DOWNSAMPLE 0.375 → 0.75 silently halved segment's mask erosion (2 px meant ~5
            full-res, then ~2.7). Pixel constants must scale with the image. Erosion now does
            (5 px per 1280 of width), so stereo at 960 and RealSense at 640 each get the right shrink.

## h08 · integration · G2 on the REAL chain: red, diagnosed, half fixed
Files:      roomctl/state.py (settle, MOVE_M), fake/scene_gen.py, roomctl/publish.py (scan trace),
            roomctl/cli.py (ROOM_SCANNER=perception:<dir>), tests/test_state.py, test_idempotent_scan.py
Verified:   perception/synthetic.py, 12 scans, network blocked, serialize.write_tree wrapped in a
            scratch harness: per-field hysteresis → 8/11 rescans dirty; + object-level settle
            (MOVE_M 0.05) → 3/11; + 2-consecutive-miss removal → 0/11. tests/ 393 passed.
Blocked on: serialize.py calling state.settle (pointcloud); a missed-detection debounce or P10's
            multi-frame vote (segment/pipeline). The G2 perception rows stay red until then.
Surprise:   The gate was right and the rule was wrong. We had been using the quantizer's 1.5-quantum
            deadband as the "did it move?" test, and docs/20 had said all along that those are two
            decisions: a single-view centre wanders 3 cm, far past 1.5 cm. And the last phantom
            diff wasn't noise at all: a small object missed in 3 of 11 scans, each miss deleting a
            file. The fake never showed either, because its noise is sub-quantum and its detector
            never misses — the most dangerous kind of passing test.

## h05 · cloud · the robot resolves its own Sentry issues (docs/28), live
Files:      robot_sentry.py (new: SentryIssues, SelfHealingRobot, IssueMirror), obs.py (context()
            on the current scope), telemetry/hub.py (watch-loop beat opt-in), scripts/sentry_watch.py
            (skips synthetic), .gitignore (evidence/), .env.example, docs/28 status, docs/10 D34–D36
Verified:   `python robot_sentry.py demo` through roomctl's executor with a mock arm that slips once →
            GITSPACE-B: first_seen → note "🤖 Resolved by the robot … rescan came back clean" →
            set_resolved; 8 KB photo attached; heal trace with resolved_by=robot, attempts=2.
            Uptime #10384065 re-pointed to the deployed tunnel (/api/health 200); 11/11 checks
            green before the move. Tests: 42 in telemetry/ incl. 5 for robot_sentry (Sentry mocked).
Blocked on: SENTRY_ROBOT_TOKEN (a human copies it from the "gitspace robot" integration — the
            timeline says "Daniel W Liu" until then); master's cmd_apply hook for the real robot.
Surprise:   A resolve is only a headline if the ACTOR is the robot — Sentry credits whoever owns the
            token. Also: SENTRY_STORY.md's six AUTO-CAPTURED entries were our own test runs
            (VERIFICATION frames, fake-robot falls); sentry_watch now skips synthetic sources.

## h00 · elastic · LIVE again: 20/20 query tests, the one-query hybrid artifact, CLIP images confirmed
Files:      elastic/demo_hybrid.py, elastic/artifacts/hybrid_mug.{txt,json}, elastic/queries.py
            (hybrid_request), elastic/tests/test_clip_live.py, elastic/NOTES.md
Verified:   `pytest tests -W error::ElasticsearchWarning` → 132 passed; test_queries 20/20 live.
            `scene_gen.py --index` → idempotent (objects/voxels/clouds overwritten, streams "already there").
            `demo_hybrid.py mug --save`: ONE request, cup_7e21 (class cup; "small ceramic cup, cream
            coloured" | "short white cylinder, glazed" | "off-white porcelain cup, empty") = BM25 MISS,
            Jina dense #2 (0.665 vs 0.556 noise floor), #2 after RRF + rerank.
            .jina-clip-v2: 1024-d text+image, each drawn image nearest its own text (test passes).
Blocked on: a real-repo guard in roomctl/publish.py (asked master).
Surprise:   Two sets of non-pipeline docs were in the REAL index. (1) a hand-injected proof:cup_7e21
            (class "vessel") had become the top hit for cup_7e21 in every search. Deleted on request,
            backed up. (2) a perception SELF-TEST published 6,807 docs through the new commit hook from a
            scratch repo, and commit_at(now) (the time-travel demo) now answers with its sha, which isn't
            in room.git. Backed up, not deleted yet. Also: CLIP images must be data URLs; Elastic's
            blog shows bare base64, which is a 400.

## h00 · perception/segment · associate: removals debounced, `moved` is exactly what settle() writes
Files:      perception/associate.py, perception/tests/test_associate.py
Verified:   `pytest` (my 6 perception files + agent/tests) → 97 passed, 1 skipped. Through the real serialize →
            settle write path: missed once → `missed`, file byte-identical; missed twice → `removed`,
            file gone. Seen again → count cleared. Occluded → not a miss. 3 cm → unchanged,
            identical bytes; 6 cm → moved; 4.4 cm onto the shelf → moved (zone); 4 cm inside the
            zone hysteresis → unchanged; yaw-only → unchanged, like the file.
Blocked on: pipeline.scan_into passing misses/zones and saving next_misses (snippet sent to master)
Surprise:   The verdict and the file had two different definitions of "moved". Mine was per-field
            1.5 quanta or a yaw turn; settle's is 5 cm object-level or a zone change. So `moved`
            could be reported over a byte-identical file. The fix wasn't a new threshold: _carry now
            CALLS stabilize_record + settle, so there's one definition.

## h09 · perception/pointcloud · G2 green on the real chain (synthetic recordings), once serialize calls settle()
Files:      perception/serialize.py (roomctl.state.settle after stabilize), perception/pipeline.py (stage_scan trace=),
            perception/tests/test_serialize.py; docs/10 P18
Verified:   `GITSPACE_RECORDINGS=<4 synthetic recordings> python3 -m pytest tests/test_idempotent_scan.py -k perception`
            → 8 passed (was: PHANTOM DIFF on rescan 1). `python3 -m pytest perception/tests` → 201 passed.
            Longer run, 12 scans seed 7: 1 of 11 rescans dirty — a one-scan spurious `added` object.
Blocked on: nothing for the settle half. The flicker half needs P10's multi-frame vote (the pipeline's, i.e. mine).
Surprise:   The fix wasn't in depth at all. Per-FIELD deadbands can't hold an object whose every field wanders 3 cm;
            one per-OBJECT "did it move 5 cm?" (docs/20 Part 4, which we'd read and not implemented) turned 8/11 dirty
            rescans into 0 on the master's run. What's left is detection flicker, which no pose rule can see.

## h10 · web · Robot Session Replay (docs/29 ★): GET /api/replay/<capture_id> + a scrubber where the robot and the Sentry trace share one clock
Files:      web/replay_api.py (new), web/pages/replay.{html,js,css} (new), web/tests/test_replay.py (new),
            web/sentry_client.py (trace_spans on the endpoint that answers live; may_start), web/telemetry_api.py,
            web/pages/capture.html (replay embedded under the page), web/pages/telemetry.js ([replay] on failure rows),
            web/server.py (router tuple)
Verified:   LIVE, three ways. (a) /replay/cap_0004 — opens on the tilt spike at −200 ms: tilt_rate crosses the gate
            in the accent, odom_residual jumps at the same instant, flags for tilt_spike (derived) / shutter /
            capture_rejected. (b) /replay/cap_82093 — 7 real Sentry spans on the shutter's clock; dragging to
            −362 ms lights `robot.capture — 3x stereo grab/retrieve`; clicking a span scrubs to it. (c)
            /replay?from=2026-09-19T01:43:34Z&to=…58Z — a real telemetry-hub burst with all 8 signals: 1045
            encoder samples integrate to a 0.536 m path turning −1.5°, the body visibly leans 14.6° at +10.8 s,
            the circle of doubt is odom_residual drawn to scale. Odometry checked in closed form (straight line =
            circumference per turn, spin in place, quarter arc ends at (1, 1, 90°), counter reset ≠ motion).
            Phone (390×844, touch): no horizontal overflow on /replay, /capture/<id>, or the git graph; a drag on
            the lanes scrubs, vertical swipes still scroll; on phones the scrubber sits directly under the robot.
            Public tunnel: /replay, /api/replay, /capture all 200. web/tests: 51 passed.
Blocked on: DATA, not code. No capture has encoders within 10 s of its shutter (encoders exist only in two hub
            bursts, 01:43:34–56Z and 05:32:40–58Z), so no capture replays with a path; and no capture has both
            telemetry AND a real trace, so "drag to the spike, robot.capture lights up" needs cap_0004's spike
            and cap_82093's trace to be the same capture. Asked gitspace-fe for both in fake/scene_gen.py.
Surprise:   docs/29 says to anchor on "the capture poses we already store in room-clouds". room-clouds stores NO
            pose (mapping: bounds, cameras, cloud_uri, …; `pose` exists only on room-objects). So the path is a
            shape from (0,0), not a place in the room, the voxel map is NOT drawn under it (placing it would be a
            guess), and the page says so. Also: Sentry's events-trace/ endpoint returns {"transactions": []} for
            traces whose spans organizations/<org>/trace/<id>/ returns in full — the telemetry board's waterfall
            was silently empty for real captures until this.
            Now that the site is public: a visitor's [ask Seer] may READ an existing run but never START one
            (a run is billed); tunnel traffic is told apart by its forwarding headers, same test as the inlet.

## h10 · web · /replay addendum: stored capture poses pin the path; downsampled windows say so
Files:      web/replay_api.py (pin, downsampled), web/pages/replay.js, web/tests/test_replay.py
Verified:   fake/ now writes room-clouds `pose {x, y, yaw°}` and −8 s…+2 s of all 8 signals (not indexed yet).
            replay_api reads that pose (yaw in DEGREES), pins the integrated path to the room at the capture's
            own pose, and reports gap_m at every other capture pose in the window — "path is N mm off" on the
            page: the integration drift, drawn. Closed-form test: a 1-turn drive pinned at (2, 1, 90°) starts
            at (2, 1 − 0.518) and a pose 3 cm away reads gap 0.03 m. web/tests: 52 passed.
            Checked live 06:16Z: robot-telemetry has ONE backing index, generation 1, and cap_0004's 5.6 h-old
            samples are still raw 50 Hz — lifecycle `after: 1h` counts from ROLLOVER, not sample age.
Blocked on: re-indexing the fixture (fake/'s and Daniel's call: regenerate vs additive backfill).
Surprise:   Elastic puts the first rollover at ≈21:20 EDT Saturday; an hour later every capture indexed before
            it — including cap_0004, the story — becomes 5-min buckets. The capture page's strip, the board and
            the replay all lose the spike at that moment unless the fixture is loaded again Sunday morning.
            The replay now says "downsampled … can no longer be replayed" instead of drawing one point.

## h00 · elastic · telemetry stays raw all event; self-test docs deleted; room-clouds pose
Files:      elastic/mappings/robot-telemetry.json, elastic/mappings/room-clouds.json, elastic/NOTES.md
Verified:   live: robot-telemetry lifecycle now after 1d -> 5m, after 2d -> 30m, still ONE backing
            index (no rollover). room-clouds `pose` {x,y,yaw} added in place. 6,807 self-test docs
            deleted after matching the backup count (0 failures). commit_at(now) -> 1a668ec0 (main)
            again. room-objects 45, room-voxels 3,004, room-events 5.
Blocked on: nothing.
Surprise:   "Raw telemetry lives 1 h" was wrong: `after` counts from ROLLOVER. Live, the oldest doc
            (~7 h) was still raw, and Serverless's rollover is max_age 1d [automatic]. The real
            hazard is a manual _rollover, which would start the clock on every capture already in it.

## h00 · elastic · "BM25 can't, the vector can" re-verified on pipeline-captured objects only
Files:      elastic/demo_hybrid.py (now proves its own provenance), elastic/queries.py (search_objects
            also returns the shown doc's commit_sha + capture_id), elastic/artifacts/hybrid_mug.{txt,json}
Verified:   live, after both deletions: all 45 room-objects docs belong to the 4 commits in room.git (0 strays).
            `demo_hybrid.py mug --save` → cup_7e21 (class cup) = BM25 MISS, Jina dense #2 (0.665; noise
            floor 0.556), #2 after RRF + rerank. At each of its 4 commits it is in room.git's tree, and its
            3 descriptions are exactly cam0/cam1/cam2's own views at that capture (shown: cap_0005 → "small
            ceramic cup, cream coloured" / "short white cylinder, glazed" / "off-white porcelain cup,
            empty"). "mug" appears in none of them. Provenance line: "4 commits … all exist in room.git."
            Live search tests 6/6 after the change.
Blocked on: nothing.
Surprise:   "Genuine" has a limit: the camera views are fake/scene_gen's scripted descriptions
            (vlm_model "fake/scene_gen"), not a real VLM. The chain capture → per-camera views → commit →
            room.git is real; the words are synthetic. Recapture with the same command once perception
            commits real captures (publish hook is live) and it will show real VLM text.

## h06 · cloud · attachment budget laptop-wide; stable URL ready for repr.ink (one manual step left)
Files:      obs.py (ledger-backed budget: 20/h · 1 MB/h · 100 KB each; attach() only inside
            capture_scope), telemetry/test_wiring.py (+3), scripts/named_tunnel.sh (new),
            docs/19 (named-tunnel steps + DNS records), scripts/README.md, DIAGRAM-DRIFT.md
Verified:   Sentry stats, month-to-date 2026-09-01 → 09-19 06:20Z: 3 attachments, 21,475 bytes =
            0.002% of 1 GB (all from the 05:00Z verification runs). A second python process on the
            same ledger got the 3rd slot, not a fresh budget. 182 tests pass across the 10 suites that
            import obs; the real ledger was never touched by tests. cloudflared validates the tunnel
            config offline; ingress routes repr.ink / www.gitirl.ink → :8000, unknown host → 404.
Blocked on: a human: register repr.ink (or gitirl.ink), add it to Cloudflare, switch nameservers,
            `cloudflared tunnel login`. Then `scripts/named_tunnel.sh repr.ink` does the rest.
Surprise:   "~13,000 attachments/day" was 13,249 BYTES — twice now. Sentry's stats report the
            attachment category's quantity in bytes; times_seen is the count. The real risk was
            elsewhere: the old cap was per PROCESS (four processes import obs), and obs.attach()
            outside a forked scope would have re-sent its file with every later event.

## h10 · perception/pointcloud · the judge's click, end to end: /capture/<id> now renders REAL pipeline output
Files:      perception/pipeline.py (capture docs: room-clouds + room-observations + clouds/<id>.ply, rejected captures
            too; opt-in es=/GITSPACE_INDEX_CAPTURES), perception/es_sink.py (the shared retry/spool client, from
            voxelize), perception/voxelize.py (VoxelGrid.from_docs, voxels_for_commit), perception/synthetic.py
            (web-valid ids, --now), tests: test_pipeline.py (8, fake ES) + test_capture_live.py (6, live); docs/10 P19
Verified:   `GITSPACE_LIVE=1 python3 -m pytest perception/tests/test_capture_live.py` → 6 passed against the running
            server + live cluster + live Sentry: a fresh capture's /capture page 200; /api/capture/<id> synthetic=false,
            gate = the recording's values, 3 objects with their own raw positions, point_count = its .ply, sentry.url =
            its own trace; /api/replay == raw robot-telemetry sample-for-sample, 20 ms apart, for every capture in
            room-clouds; costmap + base pose on live room.git HEAD from live room-voxels; /api/graph == git log.
            Test docs deleted after (0 synth_* left). `python3 -m pytest perception/tests` → 221 passed.
Blocked on: a camera. Every capture already in the index is fake/scene_gen, all telemetry is synthetic, no frames.
Surprise:   Before this, not one real capture could open the page that "wins both prizes": it reads room-clouds and
            room-observations by capture_id, and the only writer of either was the fake. The replay IS exact — its
            1 s holes are the fake telemetry's (only ±2 s around each fake shutter), not decimation. And the live
            costmap is empty: the fake room's voxels have no floor or table legs, so the collision filter never fires.

## h00 · elastic · real camera text is BLOCKED ON HARDWARE, so synthetic provenance is now explicit everywhere
Files:      elastic/mappings/room-objects.json (+vlm_model), elastic/records.py, elastic/queries.py,
            elastic/demo_hybrid.py, elastic/artifacts/hybrid_mug.{txt,json}, elastic/NOTES.md, docs/10
Verified:   Real captures blocked. The Pi (PI_HOST:8080) times out on GET /pose, no real recording
            exists on disk, and evidence/ holds only Sentry attachments. All 6 indexed commits (main,
            movie-night, and master's real live-check 174302b/a2b2703) have descriptions from
            fake/scene_gen, checked per commit against their observations. room-objects `vlm_model`
            added live and backfilled 67/67 (0 failures). `demo_hybrid.py mug --save` prints a
            PROVENANCE banner (SYNTHETIC text; REAL commits and live search) and a "text by" column.
            The demo still holds: cup_7e21 BM25 MISS, Jina dense #2 (0.665 vs noise floor 0.555),
            #2 after RRF + rerank; 6 commits, all in room.git. Tests: elastic 136 passed (live),
            publish 19 passed.
            Key rotation: no such request ever reached elastic, and nothing was invalidated.
            The key in use is id 4J1Ot6ABHRDtKTe8PfFD ("gitspace"), created 2026-09-19T01:35:53Z, not
            invalidated, no expiry. No secret printed.
Blocked on: a real recording or the Pi, for real VLM text. web's SYNTHETIC badge (asked web).
            scene_gen emitting vlm_model on its own room-objects docs (asked master).
Surprise:   perception/voxelize.py's new flat `from es_sink import …` broke the publish hook
            (test_publish 11/18 failing) for anyone importing it as a package. elastic works around
            it; the fix belongs in voxelize. Also open and mine: "where are my scissors" doesn't rank
            the scissors in the top 3 (master's report). Next.

## h11 · web · the graph tests stop reading the LIVE room.git: 73 passed from any directory, whatever anyone else is doing to the room
Files:      web/tests/conftest.py (snapshot of the room story), nothing else — no product code changed
Verified:   `pytest web/tests` from the repo root, `pytest tests` from web/, and `pytest .` from web/tests/:
            73 passed each time, identical. Against a deliberately hostile room (a scratch copy with a dirty tree —
            mug modified, scissors deleted, an untracked yaml — plus an extra branch with a newer commit): 73 passed,
            and the hostile copy was left exactly as dirty as it started. The real room.git was only ever read.
Diagnosis:  The failures were real assertion diffs but not web bugs and not credentials. web/tests/test_graph_*.py
            asserted against ../room.git itself, which is shared and live:
            · 20 ERRORs "room.git must be clean" with ` M mug_a1b2 / D scissors_9f3a / ?? marker_c3d4 / ?? tool_4f2a`
              — another session had the "tidied" state sitting uncommitted in the working tree for about an hour
              (01:38 → 02:27), then committed it on a new `live-check` branch, reverted it, and checked out main
              (reflog 02:27:33 – 02:29:47). The guard fixture refuses a dirty tree, correctly — on the wrong repo.
            · then `assert 6 == 4`: `git log --all` had grown by live-check's two commits, and nodes[0] was no longer
              main's tip. Three runs in ten minutes gave 20 errors, then 3 failed + 12 errors, then 1 failed.
            Fix: conftest clones the room (read-only, --no-hardlinks) into a temp dir, pins `main` and `movie-night`
            to the demo story BY COMMIT SUBJECT (SHAs change when fake/scene_gen.py regenerates; the story does not),
            deletes every other ref, and points ROOM_GIT_PATH at it before any test module imports. Same SHAs as
            fake/out/demo.ndjson, so the fixture enrichment still lines up; the before/after fingerprint still proves
            graph_api never writes. No story in the room -> those two files SKIP with the reason instead of erroring.
            · `assert 13 == 12` (publish_commit spooling) is tests/test_publish.py — roomctl's, not web's, and I did
              not touch it. It was mid-edit by its owner (roomctl/publish.py 02:24, tests/test_publish.py 02:36): the
              cloud doc added a 13th spooled document before the expectation caught up. As of 02:40 it is 19 passed,
              alone and in the full run (472 passed). Same for a transient `No module named 'es_sink'` from
              perception/voxelize.py at 02:27.
Blocked on: nothing.
Surprise:   The guard fixture was doing its job the whole time — it was pointed at a repo four sessions write to.
            A read-only module's tests still need their own copy of what they read.

## h00 · web/landing · the Bracket Bot splat has a renderer, a loader and a harness; waiting on the trained .ply
Files:      web/landing/vendor/gaussian-splats-3d/ (@mkkellogg/gaussian-splats-3d 0.4.7, npm build byte for byte, no CDN),
            web/landing/splat.js (loadRobotSplat() -> THREE.Group: feet on y=0, +Y up, `height` tall),
            web/landing/models/robot_splat.json, web/landing/dev-splat.html (:8124),
            web/landing/tools/install_splat.mjs + tools/pack.html, tools/dev/splat.mjs, tools/dev/splat-in-scene.mjs,
            web/landing/HANDOFF-SPLAT.md. No existing file edited.
Verified:   `node tools/dev/splat-in-scene.mjs` — the robot injected into the REAL landing page draws through MangaPass,
            depth-tested against the arms: 60 fps before and after, 67 -> 69 draw calls, 0 console messages.
            `node tools/dev/splat.mjs` — roll-in from the left on the stage camera, x -8.6 -> -3.5 and stops, 60 fps,
            no request leaves 127.0.0.1. Turned-robot vs swung-camera frames identical (draw order follows the object).
            A 62-float OpenSplat-layout .ply and its packed .ksplat load and fit identically; 5.6 MB -> 0.4 MB.
            All of it on the STAND-IN (models/robot_cloud.ply, 22,473 COLMAP points turned into gaussians in the browser
            and sent through the same parser); the trained splat was still training (OpenSplat, 15000 iterations).
Blocked on: web/server.py LandingFiles.SERVED has neither ".ksplat" nor ".ply": the splat 404s on :8000 and on the deploy
            until ".ksplat" is added and the server restarted (not mine to edit or restart). Orientation of the trained
            splat has to be checked by eye in dev-splat.html once it lands.
Surprise:   There is no walk-in choreography in web/landing to drive it: the only entrance from the left was pomme's claw
            arm, retired with snakeArms.js. And the renderer only re-sorts when the CAMERA moves; the landing camera
            never does, so a robot that turns would have drawn inside-out without dynamicScene: true. Spark 2.x, the
            renderer everyone points to now, needs three >= r180; we vendor r170.

## h00 · perception/segment · identity: assigned then defended — attacked, it held; G2 green on the real pipeline
Files:      perception/tests/test_identity.py (new), perception/cluster.py (merge_split_clusters),
            perception/associate.py (sticky removal), perception/tests/{test_cluster,test_associate}.py
Verified:   (1) test_identity.py: 16 passed + 1 strict xfail. The id is class slug + sha1(class|capture|
            ordinal): no geometry, no colour. It survives 1 cm / 20 cm / 80 cm moves, a zone change (the
            file moves, same name), 4 scans occluded (byte-identical), a different camera seeing the
            other face, YOLO relabelling cup→bowl, two identical twins (distinct ids, kept; move one,
            remove one: no swap), and rescans. A new object always gets a new id; a returning one gets
            its old id only through the history search. xfail: twins swapping places, which docs/25
            says nothing can resolve. Mutations: id from position → 15 fail, gate 0.1 m → 4, re-classify
            each scan → 1.
            (2) G2, tests/test_idempotent_scan.py with the real pipeline on synthetic recordings,
            seeds 0/1/3/7/11: 11/11 rescans clean each, checked after every scan, under BOTH Pythons
            (system: OpenCV 5.0; .venv: 4.14). Whole repo: `pytest perception/tests agent/tests
            tests` → 654 passed, 22 skipped, 1 xfailed.
            (3) Per-object confidence: NO real captures exist on this machine (no RealSense session_*,
            no stereo recordings). Synthetic stereo through the real pipeline: all 15 objects seen
            11/11, `unchanged` 11/11, centre spread 5–26 mm (the tall block worst, the P15 smear), and
            record confidence = None for every one: the fallback path has no score. Real photo +
            real YOLO (3 warped views): 20 objects, confidence 0.27–0.77, 7 seen by one view only.
Blocked on: real captures for (3): one RealSense session_*/capture_NNNN or a stereo recording here
Surprise:   Seed 7's phantom was the 8×8×20 cm block splitting in two (a 56-point base fragment,
            docs/15 failure mode 2), and it failed G2 DIFFERENTLY per Python. OpenCV 4.14's SGBM put
            the fragment in the baseline, then removed / restored / removed it. OpenCV 5.0's spawned it
            at rescan 8 as `?? unknown_3028`. Fix at the source: clusters whose footprints touch with a
            ≤5 cm vertical gap are one object. Second bug, mine: a removed object's miss count reset,
            so the next scan said `missed` (file back), and an occluded scan resurrected it. Removal
            now sticks until the object is seen.

## h12 · web · the camera RECEIVER that did not exist (web/camera_ingest.py) + SYNTHETIC provenance on every card
Files:      web/camera_ingest.py (new, standalone), web/tests/test_camera_ingest.py (new), web/landing/live/ (output),
            web/store.py (provenance), web/dash_api.py, web/object_api.py, web/landing/dash.{js,css},
            web/pages/{capture,object}.js, web/pages/pages.css, web/tests/test_store_shapes.py
Verified:   RECEIVER. Read the whole sender side first: robot/server.py already serves POST /capture (inline JPEG +
            PNG16 depth in mm, intrinsics for RealSense), /frames (binary WS) and /stream; telemetry/hub.py consumes
            /stream; NOTHING in the repo ever consumed the pixels (`unpack_frame` is used only by robot's own tests).
            camera_ingest.py pulls with the existing protocol (POST /capture {"inline": true}), or reads a folder from
            Sarah's collector (docs/27), and writes latest.json + <capture>/<cam>_color.jpg + <capture>/<cam>.glb
            into web/landing/live/ — which the RUNNING :8000 already serves at /live/, so no restart. End to end
            against `python -m robot.server --sim --port 8091` (mine, stopped after): 2 cameras, 74,892 points each;
            three's own GLTFLoader loads /live/cap_0004/cam1.glb from :8000 as THREE.Points with vertex colours.
            No intrinsics (sim/replay) -> NO cloud, with the reason; --hfov-deg is the operator's explicit
            assumption and the manifest says "ASSUMED". A colour frame alone never becomes geometry. The manifest
            carries `is` / `is_not` ("a gaussian splat", "registered to the room", "fused") and `sender_mode`
            (sim | replay | hardware | recorded) so no page has to guess what it is showing. 6 tests: pinhole math,
            GLB validity, the honesty rules, Sarah's folder incl. the mm-vs-m trap.
            PROVENANCE (elastic-09's ask). One rule in store.provenance(): text is SYNTHETIC when vlm_model is
            missing or starts fake/ scripts/ tests/; a `synth_` capture is flagged "frames rendered, not a camera"
            (perception-f5's point). Search cards get an inverted SYNTHETIC chip + "what the script had each
            camera call it", one legend above the results (real: git + BM25/Jina/RRF/rerank ran live; generated:
            descriptions, poses, noise, voxels, cloud numbers); capture and object pages get the same tag.
            web/tests: 79 passed.
Blocked on: (1) the sender's address — nobody has given this machine Sarah's host yet. (2) ONE restart of :8000
            for the provenance fields (asked not to restart it right now; the JS already degrades safely — a
            server that reports no provenance is shown as SYNTHETIC, never as a camera).
Surprise:   /live/ is PUBLIC through the Cloudflare tunnel the moment a frame lands there. Real frames of a real
            room want `--out` somewhere private, or the tunnel closed, until someone decides otherwise.

## h00 · web/landing · RECOMMENDATION, nothing built yet: the robot that moves is PRIMITIVES, not a split of robot.usdz
Files:      none changed for this. Evidence only: the capture session's robot.usdz and OpenSplat checkpoints, read in
            headless Blender 4.5 and through web/landing/tools/install_splat.mjs + tools/dev/splat.mjs.
Verified:   robot.usdz is the WHOLE ROOM, not the robot: 50,000 faces, floor + a table of people + a pillar + the robot,
            ONE welded component after merging UV seams (0 loose parts). A 0.2-unit column around the robot holds
            12,082 faces (24%). Textured, that crop reads as the Bracket Bot. Untextured it is a melted candle: the wheel
            is a lump fused into the base and the floor (1,547 faces in the whole wheel-height band, tyre and hub only
            painted on), one arm is a fin, the other is gone, the head runs into the mast. There is nothing to split:
            cutting gives open shells with no inner faces, because no camera ever saw them.
            Splat side, first real OpenSplat file through the landing pipeline: robot_splat_2000.ply, 78,095 gaussians,
            19.4 MB -> 1.9 MB .ksplat, loads and renders, 0 console messages.
Decision:   Primitives, in this folder's own idiom (mech.js MAT + Rigid, heads.js). Reasons, in order:
            (1) the ink pass is near-binary, so what survives is silhouette and big light/dark shapes; the usdz's
            likeness lives in an 8k baked texture that the pass flattens to a white blob. Same lesson as the retired
            Meshy camera head. (2) what makes a roll-in read as alive is wheel spin tied to distance, the pendulum
            lean, a head that looks at GITRL, an arm wave: all four are nested Groups with primitives, only the lean
            is possible with the split mesh. heads.js already has `stereo`, "the project's own sensor": the robot's
            head can be one of the family. (3) zero download, a few draw calls, deterministic. (4) the dimensions are
            known (165 mm wheels, 425 mm wheelbase, docs/02) and the rest can be MEASURED off the scan: use the
            capture as the blueprint, not as the asset. docs/21 already said "primitives ... two wheel Groups".
            The scans keep a job where realness is the point (the capture page), not under the ink pass.
Blocked on: a yes on primitives before I build it. For the splat: the capture session's clean-up (people cluster),
            then orientation by eye in dev-splat.html; and web/server.py still does not serve .ksplat.
Surprise:   Training on masked frames teaches the splat the MASK. In the 2000-iteration checkpoint 98% of the large
            gaussians are near-black and they are ~100% of the splat's screen area: the robot (54% of gaussians, bright
            and opaque) sits inside a black shell and the page shows a void. Dropping dark-AND-large gaussians (3,605
            of 78,095; tyres and cables are dark but small, so they stay) brings the robot out. The capture pipeline's
            clean-up (clean_splat_sor.py, splat_keep_subject.py) tests opacity and neighbour density only, never size
            or colour, and these are opaque and touch the robot: it is unlikely to remove them (not yet run, so untested). Second surprise: the
            photogrammetry "robot" is a quarter of its own mesh; salient masking never touched that path.

## h06 · cloud · agent panel ↔ gitirl-agent (Andrew): contract agreed with htn:5, endpoint built
Files:      bridge/ (contract.py, andrew.py, agent_api.py, test_bridge.py — new), web/server.py (router
            tuple +1), docs/31 (AGREED + §7 finalized contract for Andrew), docs/10 D40–D42
Verified:   19 tests: graph verbs never reach his middleware (a spy fails if they do); restore plans as
            restore, never checkout/revert; revert = one commit's inverse with conflicts; a repeated
            request_id never plans twice; poses without world_z_up refused; the ws hub correlates by
            request_id and refuses a proxied stranger; the stub matches HIS real parser (run as
            `run_dev.py --jsonl`) on 11 phrases. Live on the REAL room.git, fingerprint unchanged:
            "restore b3691ea" → 3 ops served_by andrew:jsonl; "revert HEAD" → graph path, gitspace.
Blocked on: web restart (router registered, not loaded); a `study` tag in room.git (D41);
            Andrew adopting docs/31 §7; who owns verify/retry (docs/30 §2) — the one open decision.
Surprise:   Andrew's middleware isn't an endpoint we call — it's a WebSocket CLIENT that dials us,
            and its dev orchestrator answers "Desired state restored and verified" for a restore
            that moved nothing. And the existing node-graph `revert` undoes everything after the
            commit, not the commit.

## h00 · elastic · key rotation stopped at step 1 (by design); "where are my scissors" fixed on real data
Files:      elastic/rotate_key.py, elastic/ROTATION.md, elastic/setup_elastic.py (connect(admin=), pipelines),
            elastic/pipelines/room-objects-rerank-text.json, elastic/mappings/room-objects.json
            (rerank_text + default_pipeline), elastic/queries.py, elastic/records.py, elastic/tests/*, docs/10 D38
Verified:   ROTATION. Step 1 cannot be done from elastic's tooling. Elasticsearch: "If the credential that is
            used to authenticate this request is an API key, the derived API key cannot have any
            privileges", and elastic's tooling only holds an API key. Stopped per instruction; old key
            4J1Ot6ABHRDtKTe8PfFD still live, nothing invalidated. Prepared: least-privilege role
            descriptors (runtime + admin) in ROTATION.md for Kibana, and rotate_key.py promote / verify
            / retire / rollback, which never prints a key. `rotate_key.py verify` against the current key:
            8/8 PASS (identity, privileges, hybrid search + ES|QL, idempotent index + create, publish-hook
            client, no stale long-running processes, web /api/health).
            SCISSORS. The cause was the reranker, not the filler: BM25, dense and RRF all had scissors #1.
            text_similarity_reranker reads only raw_description[0] ("orange plastic handles, steel blades").
            Fixed with an ingest pipeline that writes rerank_text = class + every description, which the
            reranker scores; backfilled 67/67. Live, main: "scissors" hammer→SCISSORS #1; "where are my
            scissors" #4→#1; keys/mug/tape/hammer phrasings #1; mug→cup_7e21 demo unchanged (BM25 MISS,
            dense #2, final #2). `pytest tests` → 146 passed live, incl. test_search_objects_conversational.
Blocked on: a person minting two keys in Kibana (ROTATION.md §1), then promote → restart web + hub →
            verify → retire.
Surprise:   Not the fix I expected. Filler words weren't diluting BM25, which ranked the scissors first
            every time. The cross-encoder had never seen the word "scissors": it reads one value of a
            multi-valued field. Also: master's live-check a2b2703 published scissors_9f3a with no
            descriptions (a scanner meta gap).

## h00 · perception/segment · the voxelize regression that broke the commit hook, fixed at the source
Files:      perception/voxelize.py (import block), perception/tests/test_imports.py (new),
            elastic/records.py + elastic/tests/conftest.py (their sys.path workaround REMOVED, nothing else)
Verified:   Workaround removed, project venv: tests/test_publish.py (the commit hook) 19 passed; elastic
            test_records 25 + test_mappings 64 + test_connect/query_shapes/rotate_key → 115 passed;
            test_imports 6 passed (fresh interpreters: package import with only the repo root, flat,
            both styles in either order sharing one es_sink, pipeline's es_sink-first order, records.py
            with no perception/ on sys.path). Whole repo (system Python): 693 passed, 23 skipped, 1 xfailed.
            elastic's LIVE files (test_ingest/test_queries/test_clip_live) not run by me: elastic-09
            was busy and its fixture tears down the shared test- indices. Asked them to re-run.
Blocked on: nothing
Surprise:   The "fix" that turned the suite green was itself a bug. 02:27 made a flat
            `from es_sink import …` (crash as a package). 02:40 made it try-relative-except-flat (no
            crash). But the commit hook imports voxelize BOTH ways in one process, so it got two es_sink
            modules. Live: an es_sink.Offline raised by one copy sailed past `except es_sink.Offline`
            in the other. And elastic's sys.path workaround hid all of it. The fix: load the sibling
            by path, register it under both names, so there's one module and errors aren't masked.

## h11 · perception/pointcloud · the room graph as a control surface: preview → stage → commit, on a dev page
Files:      web/landing/dev-graph.html (new dev page on the :8124 landing server), perception/devgraph.py (backend,
            127.0.0.1:8125 only), perception/tests/test_devgraph.py (10, on a COPY of room.git);
            voxelize/costmap/raycast package-safe imports (the es_sink break in roomctl/publish)
Verified:   http://127.0.0.1:8124/dev-graph.html → 200. Against the REAL room.git and indices, read-only: graph = git
            log (6 commits, both branches) + room-events/room-clouds enrichment (capture ids, quality_ok) with web
            :8000 down; restore→b3691ea = 3 ops (marker add, mug move, scissors remove) — the same 3 the cloud
            session's docs/31 planner got independently — with pick/place stances from HEAD's live room-voxels;
            revert HEAD = 3 ops, 0 conflicts. Write path tested only on a copy: stage refuses a moved HEAD or a
            dirty tree, abort restores byte-for-byte, commit records exactly the target on top of the previewed base,
            revert undoes ONE commit (not "back to it"). The real room.git was never staged or committed here.
Blocked on: web :8000 restart (docs/31's /api/agent/command is built, not served); a `study` tag in room.git for
            "set my room back to study mode"; an executor for the robot; a scan-less publish for graph-made commits.
Surprise:   Every stance the page shows for today's room is under the desk top — HEAD's indexed voxels hold no floor or
            legs, so the costmap has 0 obstacles and nothing is collision-checked. The page now says exactly that
            instead of drawing confident blue squares. And the web's own /api/command "revert" of an older commit
            plans "go back to it", undoing everything since: a different operation wearing revert's name.

## h00 · perception/segment · .roomignore is read: what is deliberately NOT an object (docs/25 §6)
Files:      perception/segment.py (roomignore(), lift/run `ignore=`), perception/associate.py (for_serialize
            `ignore_paths=`), perception/tests/test_identity.py
Verified:   room.git's .roomignore parses to labels {person, robot, cable} + globs (zones/floor/**). A "Robot"
            mask never becomes an instance (case-insensitive). A NEW object on the floor gets no file, while
            one already tracked stays tracked, as .gitignore works. Whole repo: 698 passed, 23 skipped,
            1 xfailed. Cross-checks from other sessions: elastic's LIVE suite 146 passed with the voxelize fix and
            no workaround (elastic-09); the pointcloud session closed P18 (G2 0/11, five seeds).
Blocked on: pipeline.scan_into passing ignore_paths (sent to the pointcloud session)
Surprise:   segment.py hard-coded {"person"} while room.git's .roomignore also says robot and cable.
            The robot's own arm in view would have been committed as an object.

## h11 · perception/pointcloud · agent panel live end to end; .roomignore honoured by the pipeline
Files:      perception/devgraph.py (relay passes docs/31 errors through at any status), perception/pipeline.py
            (.roomignore path globs → associate.for_serialize), perception/tests/test_devgraph.py (+1)
Verified:   with :8000 back, through http://127.0.0.1:8124/dev-graph.html's backend (plan-only, room.git untouched):
            "revert HEAD" → graph path, served_by gitspace, 3 ops, never sent to Andrew; "restore b3691ea" →
            Andrew's real parser (andrew:jsonl) → restore b3691ea → 3 ops, the same 3 as the page's own preview;
            "set my room back to study mode" → andrew:jsonl → restore study → not_found at executor (no `study`
            tag). `python3 -m pytest perception/tests` → 255 passed; G2 perception row passes.
Blocked on: the `study` tag in room.git (the user's call); executor; scan-less publish.
Surprise:   The relay's first cut treated a 404 as "endpoint not live" — but docs/31's errors keep the body, so a
            state that doesn't exist is an answer from a live bridge. It said "served by NOBODY" for the one demo line
            that had actually reached Andrew's parser.

## h10 · integration · live: a real commit and revert through Elasticsearch and Sentry (GAP 4 was never a gap; GAP 1 is closed)
Files:      roomctl/cli.py (search, publish --flush, read-only passthroughs, obs transaction per verb),
            roomctl/publish.py (room-clouds doc from the staged scan.json, one hook for every scanner,
            is_the_room guard), tests/test_publish.py, tests/test_idempotent_scan.py (checks per scan)
Verified:   room.git branch `live-check`, keys live: `checkout -b` → `status` → `diff` → `commit` 174302b →
            `revert HEAD` a2b2703 (mock robot) → `search` → `checkout main` (nothing to move). Each commit
            published 11 room-objects + 1 room-events + 1 room-clouds + ~1,826 room-voxels, every doc on a
            real Sentry trace (782faf40…). main and its movie-night conflict untouched. tests/ 402 passed.
Blocked on: D39: room.git's room.yaml has no bin/home, so a revert there can't clear the untracked scissors
            (a human call). D38 search ranking: elastic has since fixed it at the reranker.
Surprise:   I told you G2 was green on seed 7, and it wasn't. My harness asserted once per BATCH of
            rescans, so a one-scan spurious add (P18, rescan 8) appeared and vanished between checks.
            Now it asserts after every scan: seed-0 ×4 green, seed-7 ×12 red at rescan 8. The other
            near-miss: a scratch repo published 6,807 docs into the shared indices the moment the keys
            came back (D37). Publishing now refuses anything that isn't $ROOM_GIT_PATH.

## h11 · integration · publication audit: 0 secrets anywhere; audit_architecture 0 FAIL; ANDREW-HANDOFF.md v1
Files:      ANDREW-HANDOFF.md (new), roomctl/executor.py (Plan.to_dict → gitspace.plan/1, `--plan-only --json`),
            docs/10 (D37–D39)
Verified:   scripts/check_keys.py: 6 usable, 8 flagged (unused or parse quirks, none leaked). A secret scan
            over 184 tracked files, 143 untracked and 187 historical blobs in 4 commits found 0. .env is
            ignored and was never committed. audit_architecture.py: 17 ok · 2 warn · 0 FAIL.
            docs/16 §3b diffed verbatim into the handoff. No git commit or push, as instructed.
Blocked on: elastic/artifacts/ (a 4.1 MB ES backup) is NOT git-ignored. Most of the work is uncommitted.
            Both are for a human to decide before anything is pushed.
Surprise:   The scanner's first 'secret' was the word "task-zero": `sk-` inside a word. A secret regex
            without a word boundary finds your prose before it finds your keys.

## h12 · integration · the command set is final: `restore` is not `revert`, `room restore` built, the envelope pinned
Files:      roomctl/cli.py (`room restore [<ref>]`, restore_commit), roomctl/executor.py (plan `frame` is the
            token `world_z_up`, prose in `frame_def`), tests/test_cli.py (+6), tests/test_executor_order.py,
            ANDREW-HANDOFF.md (§1 command set, §2 envelope: rewritten), docs/04, docs/30 §3a, docs/10 D43
Verified:   tests/ 459 passed, 8 skipped. bridge/test_bridge.py 19 passed. Scratch repo: `room restore study`
            → `[main cdd8c7b] Restore study` on top of `afternoon`, 2 ops, then the honest "2 of 3" (the
            marker left the room). `test_revert_is_not_restore` runs both verbs on the same c1→c2→c3
            history: revert c2 puts back only the mug, restore c1 puts back mug AND cup. §3b re-diffed
            verbatim against the current docs/16. audit_architecture: 18 ok · 1 warn · 0 FAIL (the roomctl
            ↔ docs/04 drift is cleared).
Blocked on: sending `robot_action {plan}` over /ws/gitirl-agent isn't built. roomctl drives the Pi directly
            until his agent connects. D41: room.git has no `study` tag, so the demo line returns 404 (a
            human's call which commit). Andrew: widen target_state to the ref charset, and stop sending
            command_result for text.
Surprise:   `restore` had three meanings in our own docs before Andrew's enum ever came into it: docs/30
            paired it with `revert HEAD` / `checkout`, docs/31's draft planned it as `checkout` (which
            detaches HEAD on a tag, so the next commit lands off main), and the CLI refused it. The first
            `room restore` I wrote was a fourth: robot-only, no commit. htn:5/htn:7 landed "a new commit on
            HEAD" twenty minutes later, and theirs was better: history keeps the undo, and the robot only
            ever moves toward a committed tree, the same rule as every other verb. So I rewrote mine to match.

## h13 · web · the git graph sends COMMANDS through the middleware (docs/31); `revert` finally means revert; :8000 was down and is back
Files:      web/landing/graph.{js,css} (the command console), web/graph_api.py (_revert, restore, base_sha, skipped),
            web/tests/test_graph_api.py, web/pages/{capture,object,replay,telemetry}.html + object.js (dashboard links),
            web/API-FOR-PAGES.md (new: read-only endpoint schemas for page builders)
Verified:   OUTAGE. :8000 stopped answering between 02:48:22 and 02:52 — no "Shutting down" in the log, so it was
            killed, not stopped, and not by this session. Nothing was listening, the tunnel was serving errors; I
            started it again at 02:52 (ownership-checked script). That start also took the bridge router live.
            CONSOLE. Selecting a commit now offers real commands — `restore <sha>`, `revert <sha>`,
            `checkout <branch>`, `cherry-pick <sha>` — as chips over an editable `room>` line. The TEXT is what
            travels: POST /api/agent/command (Andrew's envelope, a fresh request_id per send). The panel shows which
            decipherer is armed BEFORE sending (GET /api/agent/bridge → andrew:jsonl, his parser, live), then the
            path (MIDDLEWARE / GRAPH), who served it, the hop trace (panel → route → decipher → executor, with ms;
            the failing hop in the accent), the plan's ops with from→to poses, conflicts "will NOT be touched — why",
            the git equivalent, and the Sentry trace id. Step two is separate, armed after 600 ms, ignores
            double-clicks: POST /api/command with the preview's base_sha (409 head_moved if HEAD moved) — and if
            the verb is not on WEB_ALLOWED_COMMANDS it says so instead of pretending. Headless Chrome, 1440 and
            390 wide, against a second instance on :8001: restore b3691ea → MIDDLEWARE · andrew:jsonl · 3 ops;
            revert b3691ea → GRAPH · 1 op (tool_4f2a put back) → job queued, executor not_connected;
            cherry-pick a83a257 → 4 ops + "mug_a1b2 will NOT be touched — 19 cm from where this expects it".
            REVERT (docs/10 D40, reported by the cloud session — they were right). `revert <older sha>` planned
            _ops(HEAD, sha): a RESTORE. For "the bench, tidied" that was 3 ops undoing the afternoon and never
            bringing back the tool the commit had put away. Now graph_api._revert = the inverse of that ONE
            commit, each op tried against HEAD (applies / already_applied / conflict with the reason), sharing
            _try_ops with _cherry_pick; `restore` is its own verb; the job reports `skipped`. web/tests 80 passed,
            bridge's 19 still pass on the refactor.
            LINKS. The landing restructure (hero at /, dashboard at /?info) had silently broken every
            Status / Search / History link on capture, object, replay and telemetry — they landed on a locked
            hero. All now point at /?info#….
Blocked on: ONE restart of :8000 for graph_api's /api/command changes (the console's preview is already right — it
            is the bridge's plan; the queue step on the running server still plans the old revert). I have been
            told the user restores :8000, so I have not restarted it again.
            `restore` and `cherry-pick` are not in WEB_ALLOWED_COMMANDS (.env): previewable, not queueable.
Surprise:   zsh gives every subshell the same $RANDOM, so my five "different" probes of the bridge shared one
            request_id — and the bridge returned the first answer five times. Its idempotency works.

## h00 · elastic · every test suite green; where the key rotation actually stands
Files:      elastic/ROTATION.md (live key status)
Verified:   elastic/ LIVE, ElasticsearchWarning = error → 146 passed. Everything else (tests, perception,
            web, telemetry, bridge, agent; their live tests skip without GITSPACE_LIVE=1) → run 1:
            1 failed (tests/test_robot_events.py::test_open_and_close_are_obs_spans_with_what_happened),
            875 passed; run 2: 876 passed, 20 skipped, 1 xfailed, 0 failed. That test passes alone and
            its file passes 3/3. The file was written at 02:59, during run 1, so the likely cause is a
            file changed mid-run. Not proven.
            Rotation, live: .env still authenticates as the LEAKED key 4J1Ot6ABHRDtKTe8PfFD (unrestricted,
            not invalidated). No new key minted: from an API key, Elasticsearch only allows a derived
            key with the creator's full privileges, which fails "minimum privileges", so it stopped per
            instruction. A second valid unrestricted key, OJ06t6ABHRDtKTe8P-6I ("HackTheNorth"), also exists.
Blocked on: a person minting the two keys in Kibana (ROTATION.md §1).
Surprise:   There's a second live, unrestricted key nobody had mentioned. It's outside the rotation's scope
            unless you decide it goes too.

## h14 · web · `voxel_api` reserved; a broken optional router can no longer take the site down
Files:      web/server.py (mount_router, OPTIONAL_ROUTERS, GET /api/routers), web/tests/test_routers.py (new),
            web/landing/graph.js (ok:false + known_states), web/API-FOR-PAGES.md
Verified:   `voxel_api` is in the optional-router list: it mounts on the first restart after web/voxel_api.py
            exists, and is "not present" until then. The loader used to RAISE when a router existed but failed
            to import — with four sessions adding routers and the user restarting the server themselves, one
            typo would have been no site at all. Now a missing module is "not present", a broken one is
            "FAILED: <exception>" with its traceback in the log, and both are skipped; GET /api/routers reports
            each one plus when the process started. Tested with a syntax error, a missing dependency, a raising
            init() and a missing package. The graph console keys its error styling on the body's ok:false (the
            bridge now answers typed outcomes with HTTP 200) and turns error.details.known_states into
            one-click `restore <state>` chips. web/tests: 82 passed.
Blocked on: the user's restart of :8000 (still the 02:52 process): /api/routers, graph_api's true revert on the
            queue step, and voxel_api once written, all wait on it. There is no hot-reload, by design.

## h12 · integration · D44: an object the scan never saw keeps its words (descriptions carried forward at delivery)
Files:      roomctl/publish.py (carry_descriptions, called from _deliver; Published.carried), tests/test_publish.py (+3),
            docs/10 D44 (resolved)
Verified:   tests/ 479 passed, 8 skipped. Read-only against the LIVE cluster (one search, nothing written):
            scissors_9f3a and mug_a1b2 filled with their fake/scene_gen descriptions, an unknown id left blank.
            That checks the query on the real mapping (exists on raw_description.text, collapse + sort), which
            the mocks can't. elastic confirmed the rerank_text pipeline takes carried words like observed ones.
Blocked on: nothing. The existing a2b2703 doc is left as it was (live-check branch, not main).
Surprise:   The fix only works if it runs where the network is. Filling at build time would have looked
            finished, but a commit spooled while ES was away would then be flushed hours later with the
            blanks baked in. So the lookup runs at delivery, and the spool test checks exactly that case.

## h07 · cloud · stable URL on Tailscale Funnel; agent endpoint fixed for real phrases; room.git off the laptop
Files:      bridge/agent_api.py + contract.py + andrew.py (200/ok:false, known_states, state-name
            normalisation, CLI-prefix strip, graph_api._revert, parsed_command is terminal, nearest
            declared frame), scripts/room_backup.sh + test_room_backup.py (new), docs/19 (Funnel),
            docs/31 (status rule), docs/32 (new: state & durability), .env (WEB_PUBLIC_URL)
Verified:   Funnel https://daniels-macbook-pro.tailaa0f4f.ts.net:8443 → :8000 (443 → 3001 untouched):
            via the PUBLIC ingress /api/health 200, loopback inlet 403, agent WS upgrade 403 (localhost
            101). Uptime #10384065 re-pointed. The five black-box phrases on a fresh full app: all 200;
            "set my room back to study mode" + "restore study-mode" → restore plan, 3 ops (tag
            `study` → b3691ea, the user's go-ahead). room.git → GitHub DanielWLiu07/room (private):
            3 branches + tag identical; launchd pusher running. Tests: bridge 20, scripts 2.
Blocked on: :8000 restart for the endpoint fixes (the user said they would); the code repo's 155
            uncommitted paths (a human's commit); AWS for point clouds; the Pi remote for R1.
Surprise:   The 404s were "no such state", not routing — the request reached the handler and Andrew's
            parser was right. And zsh's $RANDOM repeats inside a pipeline, so a black-box loop can
            send five commands with ONE request_id and get the first answer five times: the dedupe
            working as designed looks exactly like a routing bug.

## h07 · robot · robot/server.py built: three cameras latched together, gated on the Pi, runs with no hardware
Files:      robot/server.py, capture.py, jobs.py, sim.py, config.py, NOTES.md, README.md (all new but README);
            tests/test_robot_capture.py, tests/test_robot_server.py; docs/16 (§2.1 as built, §2.7, §2.8, §3.1,
            §3b, §6, §8), docs/22 §8, docs/27 actions; DIAGRAM-DRIFT.md. Doc reconcile to code: docs/13, docs/20
            (asked), docs/04, docs/15, docs/23, web/PAGES.md (the other 5 pairs behind the audit's drift warn).
Verified:   `python3 scripts/audit_architecture.py` → 19 ok · 0 warn · 0 FAIL (was 17/2; the drift warn hid 9
            stale pairs behind "+5 more"). Offline suite 996 passed (tests 479, telemetry 40, perception 258,
            web 100, agent 3, elastic-offline 116); the 25 elastic live-cluster tests error with no key, as before.
            `python -m robot.server --sim` + bare `curl -X POST :PORT/capture` → 200 in 0.21 s, 16 frames (2 cams
            × 4 latches × colour JPEG + png16 depth mm), skew 0.003 ms, quality_ok. The REAL telemetry/hub.py
            against it: 22 batches, 0 gaps, 0 bad messages, and hub.peak(tilt_rate) around my shutter from ITS
            ring = 0.022162 = the Pi's tilt_rate_max exactly — t_capture_mono is on the telemetry clock.
            Latch order asserted: grab×3 → pose → retrieve×3; three 50 ms decodes → <10 ms skew; the control
            (same cameras round-robin) → >80 ms. `POST /sim/bump` then /capture → attempt 1 rejected
            (tilt 0.13), retried in the quiet window, 200. Real sentry_sdk into an in-memory transport: the
            transaction carries capture_id, robot.latch/retrieve/capture_gate spans, skew_ms/tilt_rate_max/
            coverage/capture_attempts measurements; a rejected one is tagged capture_rejected.
Blocked on: hardware. V4L2Camera and RealSenseCamera (D415 816612060665 · D435 938422076694) have NEVER run
            against a device — pyrealsense2 is not even installed here. docs/22 §7 step 5 (the hand-wave) is
            still the only thing that proves sync. On the Pi the gate needs ROBOT_TELEMETRY_SOURCE pointed at
            the balance loop; without it tilt is unmeasured and every capture is rejected, by design.
            /drive /arm /say /led are simulated; on hardware they answer 503 backend_unavailable.
Surprise:   robot/telemetry.py's FakeRobot can NEVER pass the capture gate: tilt_rate is the finite difference
            of a noisy pitch (3 mrad / 20 ms = 0.2 rad/s), so the peak over any ±100 ms window is ≥0.18 vs the
            0.05 limit — measured 0 of 139 windows. Wired in as the sim source it would have rejected every
            capture forever and looked like a broken gate. Second: docs/22's two sync mechanisms are both
            subtly wrong. BUFFERSIZE=1 makes an idle camera's grab() return the OLDEST frame, not the newest
            (the driver has nowhere to put the next one), and stamping time.monotonic() "right after grab()"
            measures when we ASKED — with a microsecond latch loop it reports ~0 ms skew whatever the
            cameras did. Fixed by a pump thread per V4L2 camera stamping arrival, time_of_arrival for
            RealSense, and "newest frame >250 ms old = camera_unavailable". Expect real skew_ms in the
            milliseconds-to-tens (free-running 30 fps cameras are up to 33 ms apart), not the doc's 1.4.
            Third: with no DSN obs.trace_fields() still returns ids (elastic/NOTES.md found it too) — so a
            capture doc would carry a link to a trace nobody sent; the rig attaches them only when live.

## h07 · robot · GET /events — the Pi's structured stream as SSE, beside the WebSockets; link blocker diagnosed
Files:      robot/events.py (new), robot/server.py (route + one tap task + /healthz.events),
            tests/test_robot_events.py; docs/16 §1 + §3c + §8, docs/23 §9, robot/README.md, tests/README.md
Verified:   17 tests. Real curl against `python -m robot.server --sim --host $(tailscale ip -4)`:
            `curl -sN '…/events?types=hello,telemetry&limit=2'` → retry, hello, <boot>:23, <boot>:24, exits;
            events arrive +0.08 +0.18 +0.28 s (it streams, not one blob); `-H 'Last-Event-ID: <boot>:33'` →
            :34, :35; a filtered idle stream gets `: keepalive`; `Last-Event-ID: 0ldb00t:5000` → new hello,
            gap{boot_changed}, then <boot>:1…; clients 0 → 1 → 0 when curl is killed; bound to the tailnet IP,
            http://10.36.20.56:PORT is refused. /stream and /frames untouched (hub + frames tests still green).
Blocked on: the Pi joining the tailnet (LINK session owns provisioning; PI_HOST in .env still 192.168.2.10).
Surprise:   The "blocker" was not the wifi: 192.168.2.10 is on no network this laptop touches — the OS sends it
            to the campus gateway 10.36.0.1 — and .env.example's LAPTOP_IP=192.168.2.24 shows the plan assumed
            both machines on our own router. And Tailscale was ALREADY up on the laptop (100.117.116.94, with
            last hackathon's `ht6-2026-rpi` still in the tailnet); netcheck on eduroam says UDP: true,
            endpoint-independent NAT — direct WireGuard paths are possible, not just DERP. On ids: a bare
            incrementing integer cannot survive a Pi restart — n restarts and "1234" resumes across two
            runs, skipping what was never seen. Ids are <boot_id>:<n>; a stale boot gets everything the new
            run has logged plus a `gap` that says what was lost. Also caught in my own code by a test:
            `sent += 1` AFTER the yield never counts the last event of a client that disconnects.

## h00 · perception/segment · doc drift reconciled — architecture audit 19 ok · 0 warn · 0 FAIL
Files:      docs/15-segmentation.md, docs/18-sentry.md, DIAGRAM-DRIFT.md (one line). No code changed.
Verified:   `python scripts/audit_architecture.py` → **19 ok · 0 warn · 0 FAIL**. The drift check compares
            mtimes, so each doc was checked against the CODE, not touched:
            docs/15 ↔ segment.py, cluster.py, merge.py. The "As built" section was mostly right; four
            things were wrong or missing. Fixed: failure mode 2 is BUILT (merge_split_clusters,
            SPLIT_XY 1 cm / SPLIT_Z 5 cm). .roomignore labels → lift/run(ignore=) and globs →
            for_serialize(ignore_paths=), .gitignore semantics. `rejected_reason` is ALWAYS null (the
            doc said merge.py sets it; it writes None). merge.py's as-built rules (never the same
            camera, complete linkage, unknown absorbs, BOX_MARGIN 2 cm, per-view rows with explicit
            nulls). Yaw = the min-area-rectangle axis, not PCA. The missing constants.
            docs/18 ↔ obs.py (the flagged pair): new section "obs.py as built", every public call as the
            code behaves (init no-op on a non-URL DSN; capture_scope stamps span + transaction + every
            obs.span; attach refused outside capture_scope, laptop-wide 20/h · 1 MB/h · 100 KB;
            agent_tool = gen_ai.execute_tool; agent_turn a plain span while the OpenAI integration
            traces the call). Stale lines fixed in place: the agent trace (real op names + the screenshot
            trace URL), agent_tool "still open" → done, robot_failure's real signature instead of a bare
            sentry_sdk.capture_exception, robot.* spans from both robots.
            docs/23 ↔ telemetry: already reconciled by its owner (03:03). Checked: 8 signals exactly as
            listed, 50 Hz, 5-sample batches, 10 s ring, 250 ms skew, SSE 2 Hz/50, ES sink 600. No change.
Blocked on: nothing
Surprise:   The audit's drift check is mtime-only, so a `touch` would have silenced it. And docs/15's
            freshly written "As built" still said merge.py sets `rejected_reason`, when merge.py has
            only ever written None: the discard pile doesn't exist yet. Also for obs.py's owner:
            robot_failure's docstring says frames are ≤640 px, but it calls small_jpeg, whose default is
            480. The doc now says 480, which is what runs.

## h12 · integration · D45: the fake scanner can't write to the live cluster from a scratch repo
Files:      fake/scene_gen.py (FakeRoom.flush asks publish.is_the_room), roomctl/publish.py (not_the_room is now
            public), tests/test_publish.py (+1), docs/10 D45
Verified:   tests/ 480 passed, 8 skipped. The new test drives init/status/reset --hard and scene_gen --scan
            (auto and on) against a scratch repo: nothing reaches index_actions, `--es on` exits 1, and the
            room itself still indexes. With the guard disabled the test fails, so it isn't vacuous.
Blocked on: nothing.
Surprise:   D37's guard went on the two write paths I was looking at (the commit hook and the real scanner)
            and missed a third that had been there all along. The fake doesn't publish through the hook; it
            writes its own observations and clouds. The fix went in the one function every one of those
            writes passes through, not at each call site.

## h12 · perception/pointcloud · DEMO-RUNBOOK: docs/06 rehearsed beat by beat; 14 judge-facing breaks ranked; one green path
Files:      docs/DEMO-RUNBOOK.md (new), docs/demo-scenes/cup_nudged.yaml (new: messy_bench plus one reachable move),
            perception/devgraph.py (/dev/agent relays http_status), web/landing/dev-graph.html (the agent verdict keys on
            docs/31's "ok", falls back to status on a pre-"ok" build, and renders error.details.hint and known_states),
            perception/tests/test_devgraph.py (+1 test, +1 assertion).
Verified:   Every CLI beat ran on a COPY of room.git (ROOM_ES=off, ROOM_EVENTS=off). The real room.git's reflog is
            unchanged since 06:29Z. docs/06 as written fails at the judge-moves-an-object, `revert HEAD`, voice,
            `merge` and `push` beats. The green sequence: status → status --scene cup_nudged → diff → reset --hard →
            "the room matches HEAD" → status clean, re-run verbatim from the runbook. Web: every page 200 on :8000 and
            on the Funnel URL. The agent panel's new contract is live since :8000's 07:13Z restart. test_devgraph
            12/12.
Blocked on: nothing. The fixes are listed in the runbook §8, each with an owner. None are mine except #5.
Surprise:   1) The costmap works, but 4 of HEAD's 11 objects have no stance, and they're the demo's heroes: mug,
            keys, scissors, glasses case. The placeholder arm reaches 0.48 m, and inflation plus the pedestal inset
            eat 0.18 m of it, so nothing deeper than ~0.30 m into the desk is reachable. 2) With a fake scanner the
            screen shows the scene file, not what a judge moved, so "hand a judge an object" is the riskiest line in
            the pitch. 3) A leftover `live-check` branch is on GitHub as the DEFAULT branch, and its commits are in ES:
            the agent answered "keys … last commit a2b2703", a commit that isn't on main. 4) I polluted live ES once:
            a rehearsal `room status` with ROOM_ES unset wrote cap_0015 (43 docs) from the scratch copy. I deleted
            exactly those docs and flagged it to gitspace-d2; master has since shipped D45 (the fake flush now asks is_the_room).
            5) The dev page's stances aren't collision-checked (it says so): HEAD's indexed voxels hold 0 obstacle
            cells, so they contradict the CLI.

## h07 · robot · the balance-loop seam + robot/RUNBOOK.md: Sarah and Ryan can wire tilt in without the robot session
Files:      robot/RUNBOOK.md, robot/balance_source.py, robot/check_source.py (new), tests/test_robot_balance_source.py;
            robot/server.py (startup error now names the fix), robot/README.md, docs/16 §2.8, docs/23 §9, tests/README.md
Verified:   `python -m robot.check_source robot.balance_source:udp --fake-sender` → OK, gate_windows_passing 1.0,
            source() p99 0.05 ms; with nothing sending → NOT READY, exit 1. The runbook's own confirm commands,
            verbatim, against a live server fed over loopback UDP: tilt_rate on /events is five numbers, /capture →
            cap_0001 attempt 1 quality_ok true. Sender KILLED mid-run: tilt_rate → [null×5] within 100 ms, /capture →
            409 tilt_rate_max:unmeasured, /arm → not_balanced. Sender restarted, server NOT restarted → captures
            pass again. 11 new tests; tests/ 490 passed; audit 19 ok · 0 warn · 0 FAIL.
Blocked on: Sarah/Ryan adding the five sendto() lines to BB's balance loop (RUNBOOK §1). Nothing in this repo shows
            how that loop exposes its state, so the sending half is theirs; the receiving half and the checker are done.
Surprise:   The obvious implementation — sample-and-hold the balance loop's latest state — is a safety bug. If the
            loop crashes, the held tilt_rate is a CONSTANT, a constant looks like a perfectly still robot, and the
            quality gate would pass every capture on the evidence of a dead sensor: the failure is in the dangerous
            direction and completely silent. State now expires after 100 ms, so a dead loop reads as "no evidence"
            and the robot stops committing instead of committing blind. check_source fails a frozen signal for the
            same reason.

## h00 · elastic · the 25 "errors" from the repo root are gone: same 148 from root and from elastic/
Files:      elastic/setup_elastic.py (CredentialsError, connect(source=)), elastic/tests/conftest.py,
            elastic/tests/test_connect.py
Verified:   Root cause was NOT a cwd-relative path: setup_elastic already resolves .env from its own file,
            and `pytest elastic/tests` from the root passed 146/146. The errors appeared only in a
            whole-repo run: web/tests/conftest.py sets os.environ["ELASTIC_API_KEY"] = "" at import (to
            keep web offline), and pytest collects every conftest into one process before any test runs.
            Fix: the live fixture takes a usable process var, else the repo-root .env FILE; missing or
            parked credentials → pytest.skip (CredentialsError); a real failure to reach the cluster still
            errors. Now: from elastic/ 148 passed; from root `pytest elastic/tests` 148 passed; whole repo
            from root: 0 errors, elastic 148/148 live. The whole-repo run still had 1 failure:
            web/tests/test_graph_api.py::test_command_guards. It also fails in `pytest web` alone (1 failed /
            103 passed) but passes on its own; graph_api.py and the test were edited at 03:20, mid-run.
            It's web's, and web has been told.
            Rotation: NOT executed. .env has no ELASTIC_API_KEY_NEW / ELASTIC_ADMIN_API_KEY, so there's
            nothing to promote. Old key 4J1Ot6ABHRDtKTe8PfFD untouched and still live.
Blocked on: the two keys minted in Kibana (elastic/ROTATION.md §1).
Surprise:   The broken suite wasn't in elastic at all: another suite's conftest blanks a shared process
            variable. The robust fix was to stop trusting shared process state, not to fix a path.

## h13 · integration · deployment sweep: every service probed, web fixed, what's left to deploy
Files:      ANDREW-HANDOFF.md §2 (transport revised for Andrew's HTTP+SSE b4f3e07), docs/10 D46–D47. No code changed.
Verified:   LIVE, 07:15–07:25Z. Elasticsearch 9.6.0: 6 indices strict, 3 data streams, hybrid search puts the
            scissors first in 0.67 s. OpenAI: gpt-5 and gpt-realtime available. Sentry: token OK. In 24 h,
            30 errors accepted, 0 rate-limited, ~35k spans. Backend transactions arriving this minute. Uptime
            monitor on the Funnel URL is up. Public URL (Tailscale Funnel :8443) is 200. The sim robot on the
            tailnet is healthy. Suites: tests 480, bridge 23, perception 259, telemetry 40, agent 3,
            elastic-offline 106, web 103 + 1 failing (test_command_guards expects 400 for `status`, which
            graph_api now answers as a 200 read). Web restarted under .venv: /api/search 503 → 200, and the
            new process reports to Sentry. `caffeinate -ims` keeps the laptop that serves the URL awake.
Blocked on: real Pi unreachable (this laptop is on 10.36.x, not the robot router's 192.168.2.x, and no
            tailnet address is set for the Pi). Telemetry hub down with it (not pointed at the sim, which would
            fill robot-telemetry). GitHub token 401. AWS keys empty. room-clean monitor needs the watch-loop
            seat freed (D35). Andrew's job/result endpoints (D46) need your go-ahead.
Surprise:   The outage wasn't a bug in any of our code. A process from another project restarted our web
            server with the wrong Python, and every health check still said green: /api/health doesn't
            import the ES client, so only the search page knew. A health check that skips the dependency
            the demo needs reports the wrong thing.

## h15 · web · a state has one name however it is said; and a hardening pass over everything a judge clicks
Files:      web/graph_api.py (resolve_state, _state_key, STATE; `resolved` on jobs; 409 ambiguous_state),
            bridge/agent_api.py (ONE marked fallback in _resolve_state — the cloud session's file, see below),
            web/tests/test_state_names.py (new), web/tests/conftest.py (contained), web/server.py (health.search,
            human 404, no transactions for files, SSE ends at the signal), web/tests/test_routers.py,
            web/tests/test_standalone_pages.py, web/pages/{capture,object,replay,telemetry}.html + landing/index.html
            (Room link), web/pages/replay.js, web/pages/seer/decor.js (one-line NaN fix), web/API-FOR-PAGES.md
Verified:   STATE NAMES. Andrew's parser turns "set my room back to study mode" into target_state `study` and
            leaves "restore study-mode" as `study-mode`. The bridge already stripped a said "-mode"; the OTHER
            direction — ref named `study-mode`, said `study` — found nothing. graph_api.resolve_state() now matches
            the said name against the real tags and local branches under one key (case, space/underscore/hyphen,
            a trailing "mode"), returns the ref that EXISTS and how it got there (exact | alias), and returns NO sha
            when two spellings sit on two different commits (POST /api/command → 409 ambiguous_state, naming both).
            The said text never reaches git. _resolve() stays strict on purpose: the bridge treats a name it
            resolves as a real ref. Tests, on a throwaway repo with tags `study-mode`, annotated `Movie_Night`,
            `focus` + `focus-mode` on different commits: every phrasing lands; nothing is guessed; and end to end
            through POST /api/agent/command (the bridge's labelled stub of his grammar) both the natural phrase and
            the explicit one plan the SAME commit, with result.ref_resolved == "study-mode". web 106 passed,
            bridge 23 passed.
            HARDENING (headless crawl of 13 URLs × 1440 px and 390 px — JS errors, failed requests, sideways scroll,
            dead links, raw output): 33 internal links, 0 dead; no sideways scroll anywhere. Fixed what it found:
            · a mistyped URL showed a human `{"error":"not_found"…}` — now a page with the way back (browsers only;
              /api/* and programs keep the §2.7 JSON; the path is escaped);
            · /telemetry logged "computeBoundingSphere … NaN" twice: decor.js called setXYZ(i, x, y) with no z, so
              the two cable quads were NaN and never drew. One-line fix, NaN gone;
            · a bad capture id said "no such capture" twice (page + embedded replay) — the embed now removes itself.
            · /api/health stayed GREEN for seven minutes at 07:13Z while /api/search was 503 (server started with a
              Python that has no `elasticsearch`). Health now carries `search {ok, detail, python}` and is not ok
              when this process cannot run the search; startup logs "SEARCH WILL 503" with the command to use.
            · every restart filed CancelledError issues in Sentry: uvicorn waits for connections BEFORE lifespan
              shutdown, so hub.close() came 2 s late. The streams now end when the signal arrives — measured on a
              second instance with two open SSE clients: 0.23 s, no cancelled tasks (was 2 s + a traceback).
            · static files were one Sentry transaction each; they are dropped like the SSE request is.
            · "Room" is in every nav web owns; web's conftest no longer blanks credentials for the WHOLE pytest
              process (it broke elastic's live tests in a repo-root run): blank during import, real values back at
              collection end, blank again around each web test — proven with a stand-in suite in both orders.
Blocked on: a restart of :8000 for the Python half (state names on /api/command, health.search, the 404 page, the
            SSE shutdown). The deployment lead restarts it: `cd web && ../.venv/bin/python server.py`.
Surprise:   I edited bridge/agent_api.py (not mine): one fallback, marked [web session], because the fix the user
            asked for could not land on the middleware path without it and _resolve had to stay strict for them.
            Their 23 tests pass. Told them at once. git_equivalent still prints the SAID name, not ref_resolved —
            theirs to decide.

## h00 · perception/segment · the mask path is WIRED: YOLO masks per camera, then cluster on the residual
Files:      perception/pipeline.py (segment_then_cluster, scan_into segmenter=/describe=, default on where the
            model is installed), perception/segment.py (run keep=: a rejected mask gives its pixels back;
            YOLO weights from $MODELS_DIR/weights), perception/tests/test_pipeline.py (one expectation: a
            masked object's row names its camera), perception/tests/test_boundaries.py,
            docs/15-segmentation.md ("As built"), DIAGRAM-DRIFT.md (the segment-stage line, now the wired truth)
Verified:   Outcome 1 — LANDED. G2 (tests/test_idempotent_scan.py, real pipeline, defaults) on seeds 0/1/3/7/11
            under BOTH Pythons: 10/10 passed, 0/11 dirty each. The path engages by default (YoloSegmenter, no
            env var). Baseline with it on: `cup_…` (class from YOLO) + the book and block as `unknown` (the
            fallback, still found under the desk's rejected "dining table" mask) + no desk object. Same ids in
            both Pythons. Whole repo: 755 passed, 19 skipped, 1 xfailed. `GITSPACE_SEGMENTER=off` and a
            machine without the weights run cluster alone, as before (test_pipeline 8/8 all three ways).
            NOT in the default chain: VLM descriptions (GITSPACE_DESCRIBE=1, API calls). SAM 3 (not built).
            Outcome 2 — audit: 18 ok · 1 warn · 0 FAIL. Perception's drift is gone. The one warn is NEW and
            web's: web/server.py was edited at 03:31, after web/PAGES.md (03:06). The owner (web-64) is
            mid-work there; told them.
Blocked on: web/PAGES.md for the 03:31 server.py change (web-64)
Surprise:   Wiring naively would have DELETED objects. On the synthetic desk YOLO's biggest mask is
            "dining table" = the desk, and it covers everything on it. segment.run's residual excluded
            EVERY mask, so the book and block would never reach the fallback and would be declared removed.
            A mask that doesn't become an object must give its pixels back. Also: single-view masks see
            one face; the mug's footprint reads 7×5 cm against a true 9×9. Stable, but small.

## h12 · perception/pointcloud · the fallback path pinned after the mask path went default-on
Files:      perception/tests/test_pipeline.py (+1 test: test_the_fallback_path_names_the_fused_cloud)
Verified:   perception/tests 261 passed, 12 skipped. test_pipeline 9/9 in both Pythons and 9/9 with GITSPACE_SEGMENTER=off.
Blocked on: nothing
Surprise:   perception-02's `camera in ("fused", "cam0")` is right for a run that could take either path, but on its
            own it passes whichever path ran. The new test forces the fallback and requires exactly "fused".

## h00 · elastic · whole repo from the root: 1074 passed, 0 failed, 0 errors
Verified:   after web's two fixes (test_command_guards updated to the new /api/command contract;
            web's conftest now restores the real env after collection and blanks only around web's own
            tests): `pytest` from the repo root → 1074 passed, 20 skipped (other suites' opt-in live
            tests), 1 xfailed, 0 failed, 0 errors. elastic's 148 all ran live, none skipped. From
            elastic/: 148 passed.
Blocked on: key rotation still waits on the two Kibana-minted keys (elastic/ROTATION.md §1).

## h16 · web · the two captures with REAL Sentry traces now say their numbers are scripted, on every page that shows them
Files:      web/telemetry_api.py (_origin: never_ran + provenance from one read), web/pages/telemetry.js,
            web/replay_api.py + web/pages/replay.{js,css} (SYNTHETIC line in the replay header),
            web/tests/test_telemetry_board.py, web/PAGES.md (reconciled with server.py — audit 19 ok · 0 warn · 0 FAIL)
Verified:   perception-02 pointed out that cap_82093 / cap_78072 are scripts/story_demo's Sentry-story documents, not
            pipeline captures: fixed coverage, three cameras disagreeing on purpose, no (or a scripts/) vlm_model. A
            browser check of every surface found the capture page labelled them and two did not: /replay/<id> said
            nothing, and the telemetry board's cards showed no chip — for exactly the two captures whose "open in
            Sentry" link is real, i.e. the ones a Sentry judge opens first. web keeps the two questions apart:
            `synthetic` = the capture never ran, so no Sentry link is built (unchanged: both keep their real link);
            `provenance` = who wrote what is on screen. The board card now reads "scripted data · real trace", the
            replay header "SYNTHETIC text scripted by scripts/story_demo · its Sentry trace is real". Headless
            Chrome against a second instance: all three surfaces × both captures say so. web/tests: 107 passed.
Blocked on: the same pending restart of :8000 (the board and replay responses are Python).
Surprise:   Conflicting instructions between sessions, surfaced rather than resolved by me: the deployment lead told
            me D46 (job fetch + authenticated result endpoint for Andrew's remote edge) waits on the user's
            go-ahead "because it moves the robot"; the cloud session says the user asked for it and is building it
            in web/ now. I built none of it, told the cloud session what I had been told, and asked that the result
            endpoint refuse tunnel traffic without its token, since :8000 is public.

## h13 · integration · deployed: backend on GCP (us-east4, next to Elastic), frontend on Vercel, capped at CA$10
Files:      scripts/gcp_mirror.sh, scripts/deploy_vercel.sh (new), room.git/.git/hooks/post-{commit,checkout,
            merge,rewrite} (background sync, real room only), docs/19 ("As built" + what-lives-where),
            scripts/README, ANDREW-HANDOFF.md (base URL), docs/10 D48, TEAM.md (deployment → master)
Verified:   https://gitspace-five.vercel.app: / and assets from Vercel's CDN; /api/health, /api/search
            (scissors first), /api/status, /object/<id>, /telemetry and /api/agent/bridge proxied to GCP,
            all 200. SSE streams through both proxies; the loopback inlet returns 403 through both. The
            mirror's own https://8-234-158-138.sslip.io has a Let's Encrypt cert and zstd. "set my room back
            to study mode" goes through Andrew's REAL parser on the VM → restore study, 3 ops, applied=false.
            The mirror's transactions reach Sentry. The room.git hook syncs in ~6 s in the background, and a
            copy of room.git does not trigger it. Sentry Uptime #10384065 now watches the Vercel URL.
            Cost guards: CA$10 budget with 25/50/90/100 % alerts; one e2-small; Google-enforced STOP at
            2026-09-21T12:00Z; no service account; SSH only via IAP; RDP rule deleted; no OpenAI/GitHub/AWS
            keys on the box; Vercel Hobby (no overage).
Blocked on: Devpost still names the Funnel URL, which has been down since ~07:25Z (D48). The remote-edge
            job/result endpoints (D46) wait on the user's go-ahead.
Surprise:   The deploy wasn't what broke the demo URL. The venue wifi did: moving the laptop from
            10.36.x to 10.37.x left Tailscale's Funnel dropping TLS while it reported itself healthy. The
            architecture doc chose AWS so that SQS and S3 would need no keys, and neither is used anywhere
            in the code. The Google budget refused "10USD" with a bare INVALID_ARGUMENT, because a Canadian
            billing account budgets in CAD.

## h07 · robot · robot/bbos.py — camera + IMU from Bracket Bot's bbos shared memory, run against the REAL robot
Files:      robot/bbos.py (new), robot/config.py + capture.py (camera kind `bbos`), robot/check_source.py (fix),
            tests/test_robot_bbos.py; robot/RUNBOOK.md §0, robot/README.md, NOTES.md, docs/16 §2.8, docs/22 §8, docs/23 §9
Verified:   On bracketbot-0183 (Jetson Orin Nano, bbos), read-only — module source piped over ssh stdin and loaded in
            memory, nothing written to the robot, no sudo, no daemon touched: `check_source` on robot.bbos:read for 5 s
            → gate_windows_passing 1.0, |tilt_rate| median 0.0102 / max 0.0435 rad/s, source() p99 0.13 ms, pitch
            2.5°; two head-camera latches → 2560x960 JPEG, 243 KB, 27-38 ms old, 20 ms per request; readers closed.
            Units/axes MEASURED, not read off comments: d(rpy[1])/dt vs gyro[1] corr +0.99 slope 62 (≈57.3).
            9 + 12 tests; tests/ green; audit 19 ok.
Blocked on: `posix_ipc` in ~/gitspace/.venv on the robot (LINK's requirements-pi.txt) — `from bbos import Reader`
            fails without it. Then ROBOT_CAMERAS=cam0=bbos:camera.head.jpeg + ROBOT_TELEMETRY_SOURCE=robot.bbos:read.
Surprise:   Three, all from touching the real thing. (1) bbos's registry.py documents imu rpy as RADIANS; the daemon
            publishes DEGREES — trusting the comment is a 57x pitch error, and only live data (slope 62) settled it.
            (2) The robot's head camera is 2560x960, not the 2560x720 every doc and perception's calibration assume;
            depth.py raises on it. (3) My own checker FAILED the real robot: idle motors publish iq = 0.0 exactly and
            I had classed any frozen signal as "would pass every capture". Only a frozen tilt_rate is dangerous.
            Also: the five-sendto()-lines plan from an hour earlier was unnecessary here — bbos already publishes
            the gyro, so the gate gets real tilt evidence without touching Bracket Bot's balance loop at all.

## h08 · cloud · Andrew's b4f3e07 closed out, and D46 built: jobs he can poll, results he can report, one real fixture
Files:      web/jobs.py (new), web/graph_api.py (job ids, plan, frame+units, status/diff/log reads), web/events.py
            (his robot_* names → `job`), web/server.py (inlet mapping, "jobs" router), bridge/andrew.py (unbuffered
            jsonl reader, GITIRL_* scrubbed, rev), bridge/agent_api.py (resolver: exact first, no guessing,
            ambiguous_state; git_equivalent names the ref that exists), docs/fixtures/restore-job.json +
            scripts/make_job_fixture.py (new), web/tests/test_jobs.py (new, 10), bridge/test_web_edge.py (new),
            ANDREW-HANDOFF.md §2 + §2b, docs/31 (b4f3e07, §3b), docs/10 D46, scripts/gcp_mirror.sh (ships roomctl),
            .env.example; .env: GITIRL_CLOUD_TOKEN generated (never printed)
Verified:   web 118 · bridge 25 · telemetry 40 · roomctl 500 · agent 3 · backup 2, all green. Live on a
            throwaway :8099 (real room.git, temp ledger): restore study → 202 job_aba556d557b65301, frame
            world_z_up; restore study-mode → the same id, 200 replayed; result without token → 401; claim
            verify-1 → running; verify-2 → 409 claimed; success → succeeded 1/1; resend → replayed;
            contradicting failed → 409 result_conflict; ask again → the finished job. room.git untouched.
            His real parser (jsonl, b4f3e07) answers both restore phrasings → b3691ea.
Blocked on: nothing for the public URL: master shipped it at 07:58Z, and the jobs router is loaded through
            gitspace-five.vercel.app (unknown id 404, write without the token 401, a checkout job returns
            gitspace.plan/1, mock_only, world_z_up). `restore` is on neither allow-list (the user's call).
            :8000 needs a restart for all of this. Real motion stays off until JOBS_REAL_MOTION=1.
Surprise:   The same restore got the same job id on the laptop's room and on the test suite's clone of it:
            naming a job by what it would DO makes the id agree across machines with no coordination.
            And three bugs only showed up against Andrew's real behaviour, not our stub: select() missed a
            line Python had already buffered (the jsonl grace window lost his trailing message); my
            "strip -mode" alias ran before web's no-guess resolver and silently picked `focus` over
            `focus-mode`; and git_equivalent printed `--source=study-mode` for a room whose tag is `study`.

## h08 · robot · GET /camera/<name>.jpg — a live view that is not a capture; hardware pose labelled "none"
Files:      robot/capture.py (CaptureRig.preview), robot/server.py, robot/config.py, robot/sim.py,
            tests/test_robot_server.py (+6), docs/16 §2.1b + §2.1/§2.2 pose_source, robot/RUNBOOK.md §5, robot/NOTES.md
Verified:   real socket, sim: 200 image/jpeg + X-T-Mono / X-Frame-Age-Ms / X-Boot-Id, 41 requests → 3 camera reads at
            the 250 ms cap, last_capture stays None. Tests: 21 requests → 1 read; a preview DURING a 0.6 s capture
            answers from cache in <0.2 s with zero extra grab() calls and the capture completes; a 5 s old frame → 503.
            tests/ green, audit 19 ok. NOT yet pushed to the robot (LINK's push_to_pi.sh).
Blocked on: nothing for the route. The real pose: robot/pose.py over bbos slam.pose, measured not guessed (RUNBOOK §5).
Surprise:   The obvious preview — call the same Hub.request() a capture uses — would have broken captures: the hub keeps
            ONE pending event per topic, so a preview and a capture asking together clobber each other and one times
            out as camera_unavailable. And on hardware a real capture was going out with pose (0,0,0) labelled "sim":
            honest-looking, checked by nothing downstream. It is now "none".

## h13 · integration · D46 live in the cloud: Andrew's job fetch + authenticated result, through Vercel
Files:      scripts/gcp_mirror.sh (ship now carries roomctl/, the planner; req file written as the service
            user), the VM's .env (+GITIRL_CLOUD_TOKEN, +JOBS_DIR), docs/19 secrets row. The endpoints are
            cloud's (web/jobs.py, graph_api job ids, ANDREW-HANDOFF §2b), built at the user's request.
Verified:   Before shipping: web 118, bridge 25, tests 500 green; the auth is constant-time, fails closed
            (503) with no token, and returns 401 on a wrong one. After shipping, through BOTH
            https://gitspace-five.vercel.app and the VM: /api/routers → jobs "loaded (2 routes)"; GET unknown
            job → 404; POST result with no auth → 401, wrong token → 401, right token → 404 not_found, which
            proves Vercel forwards the Authorization header. A plan-only `checkout study` → job
            job_0e7bd69e259e2ec6 → GET returns gitspace.plan/1 (1 op, 2 unapplied: D39 + the marker), motion
            mock_only, frame world_z_up.
Blocked on: two switches that are the user's: `restore` is not in WEB_ALLOWED_COMMANDS (Andrew's
            plan_command("restore") → 403), and JOBS_REAL_MOTION stays unset (mock_only).
Surprise:   The first ship half-applied. The code unpacked, then a root-owned /tmp/req.txt left by the
            first install stopped the requirements step under `set -e`, before the restart. For a moment
            the box had new files on disk and an old process serving them, while /api/health said ok.

## h17 · web · the commit graph becomes a picture: the room from above, the plan drawn on it, the command's route through Andrew's middleware
Files:      web/landing/roommap.js (new), web/landing/graph.{js,css}, web/landing/index.html, web/pages/pages.css
            (+ the shared view-transitions import, asked for by the room page's builder), web/PAGES.md
Verified:   Before: a text list with an empty "Pick a commit." box beside it — nothing on the page showed a ROOM.
            Now, at /?info#history, in headless Chrome at 1440 and 390 px, no JS errors, no sideways scroll:
            · THE ROOM FROM ABOVE (SVG, to scale, from GET /api/state): zones, 11 footprints with yaw. Solid = where
              it is at HEAD, dashed = where it would be, arrow = the arm carries it (the ghost travels its arrow
              once, 0.8 s; still under prefers-reduced-motion), × / + = taken away / put back, the accent = an object
              the command will NOT touch. `revert b3691ea` draws ONE mark (+ hammer); `restore b3691ea` draws three
              (mug arrow, scissors ×, + marker); `cherry-pick a83a257` draws 4 and rings the mug in the accent. The
              difference between the verbs is visible before a word is read.
            · hovering a commit ghosts what going back to it would change (measured: movie-night → 3 arrows, 2 ×,
              5 ghosts); plan rows and map objects light each other; the map sticks while the plan scrolls.
            · SCRUB TIME over the trunk redraws the room as it was. A cold /api/state is ~1 s (a git read per
              object), so states are prefetched after first paint: the scrub answers within 120 ms.
            · selecting a commit sends its first command AT ONCE as text to POST /api/agent/command (a plan is a
              read), and the old duplicate text diff is gone — one click: the room redraws, the route lights up.
            · THE ROUTE is a rail of five stations in the commit rail's own idiom, lit in order with their ms:
              this graph → router → ANDREW · gitirl-agent (his intent, `served_by`; "bypassed — never sent to him"
              on a graph verb; a stub shown as a stub) → planner → executor (not connected). A failed trip ends in
              the accent at the station that failed.
            · phones: the room, then the commits, then the preview; the command line stacks; labels stay in frame.
            · origin/* refs no longer clutter the rows; a replayed job says it is the same job, not a second one.
            web/tests 118 passed (incl. the cloud session's jobs tests); architecture audit 19 ok · 0 warn · 0 FAIL.
Blocked on: nothing for the visual. `restore` / `cherry-pick` still preview-only until WEB_ALLOWED_COMMANDS has them.
Surprise:   The planner station shows its real time, and `restore <far commit>` is ~1.1 s: graph_api reads one git
            blob per object per side. Honest, visible, and the cheapest fix is `git cat-file --batch` — not done,
            the Python layer was to stay as it is.

## h18 · web · "clean up my room", framed as git; and the two right-hand panes handed over
Files:      web/landing/graph.{js,css} (the clean-up strip; the console can open with a given sentence)
Verified:   Daniel's framing — a room clean-up service, told as GITIRL — is already what the system does, so the graph
            now says it in one strip above the commit graph: "git status, for a room — 3 things out of place since the
            room was last clean (study · b3691ea): mug, scissors, marker. A clean room is a commit. Mess is a diff.
            Cleaning up is git restore — and every clean-up is a commit you can undo." The clean state is the first
            tag or branch named clean / tidy / tidied / study; the count is GET /api/diff HEAD→that. [Clean up the
            room] selects that commit and sends PLAIN ENGLISH — "set my room back to study mode" — through Andrew's
            middleware: headless Chrome shows MIDDLEWARE · andrew:jsonl, his station reading `restore → study`, and
            THE PLAN restore study — 3 ops drawn on the room map (mug arrow, scissors ×, + marker). It is still only
            a plan: the robot needs the second, armed click, and `restore` must be on WEB_ALLOWED_COMMANDS to queue.
            Panes: both right-hand sessions in tmux 4:web hit their usage limit (until Sep 22). Their
            scrollback is saved; each was replaced by a fresh session started with a handoff
            brief carrying Daniel's unanswered request verbatim — the room/landing pane (%35: finish and VERIFY the
            page transitions) and the Seer pane (recreated as %49 — it closed when that session exited: more arms top-left,
            very slight motion). Ownership is unchanged: they own landing 3D + pages/robot.* and pages/seer/*.
Blocked on: nothing.

## h19 · web · view transitions into / and /?info were being DROPPED — fixed without blocking render
Files:      web/landing/index.html (the view is decided in the head, before first paint)
Verified:   landing-9e (the session that took over the 3D/room pane) reported inbound cross-document view transitions
            into / and /?info being skipped, and asked for the five module scripts to move to the head with
            blocking="render". Measured before choosing (headless Chrome; pagereveal listener installed by
            evaluateOnNewDocument so a late module cannot miss it; /telemetry → target, 8 runs each):
              as it was                         0/8 into /?info · 0/8 into /
              five scripts blocking="render"    6/8 · 8/8 — first paint 0.27 s → 1.6 s on a phone profile, on BOTH pages
              view decided in the head          8/8 · 8/8 — /?info still paints at 0.27 s
            Cause: the page painted one layout, then scene.js (after 1.3 MB of three.js) hid #dashboard or #hero and
            locked root overflow; the document changed shape under the transition and Chrome dropped it. Now a
            one-line classic script adds view-hero / view-info to <html> from location.search and a small <style>
            applies the rules scene.js sets later (+ a <noscript> escape). The flash of the dashboard on / is gone too.
Blocked on: nothing. (FCP on / now reads ~1.65 s because the old 0.27 s WAS that flash; background paints at ~0.27 s and
            the first real content is the first canvas frame — a CSS placeholder in #hero is landing-9e's call.)

## h08 · robot · depth from the robot: NOT built — a read-only probe to measure it first (robot unreachable)
Files:      robot/probe_bbos.py (new), tests/test_robot_bbos.py (+2), robot/RUNBOOK.md §6, robot/README.md
Verified:   units inference tested (mm / m / cm derived from camera.points' z; without points it answers
            "probably … Do not build on this"); full probe run against a fake bbos. NOT run on the robot:
            `ssh 10.37.101.235` → Network is unreachable (laptop left the venue wifi; tailnet share not accepted).
Blocked on: the robot being reachable. Then one ssh command (RUNBOOK §6) answers the four unknowns and the depth
            payload in robot/bbos.py is an hour's work — which makes /capture's gate "full" and lets
            web/camera_ingest.py build the 3D scene with no change on its side.
Surprise:   The request arrived with every hard fact missing — depth dtype, UNITS, which image it is aligned to, its
            lag behind the colour frame, where the intrinsics are — and the robot offline. Writing the payload anyway
            would have produced code that runs, ships a PNG, and is wrong by 1000x or by half an image with no error
            anywhere. bbos's rpy comment was already wrong once. The prep that is actually useful is making the
            measurement a one-liner.

## h20 · web · view-transition opt-in inlined on every page web owns; both right-hand panes handed back with context
Files:      web/pages/{telemetry,capture,replay,object}.html, web/landing/index.html, web/server.py (404 page, next
            restart), web/landing/HANDOFF-ROOM-PANE.md, web/pages/seer/HANDOFF-SEER-PANE.md (new)
Verified:   landing-9e found inbound transitions into /telemetry and /capture/<id> still dropped intermittently
            ("ViewTransition opt-in disabled"): those pages only learnt `@view-transition` through pages.css → @import,
            which can arrive after Chrome has decided. The rule is now INLINE, first thing in each <head>, with a
            one-line handler that swallows a cancelled transition's rejections. Headless Chrome, listener installed
            before any page script, 8 runs per hop: /?info→/telemetry 8/8 · /robot→/capture/cap_0004 8/8 ·
            /telemetry→/replay/cap_0004 8/8 · /capture/cap_0004→/object/mug_a1b2 8/8 · 0 transition errors logged.
            Panes: Daniel restarted the original sessions in both right-hand panes of tmux 4:web; they came
            up with no context. Each now has a handoff file in its own folder, written from the two interim sessions'
            transcripts: what it owns, the unchanged rules, and what was done while it was out — transitions finished
            and verified, /robot opens on the voxel room, four more Seer arms top-left with a slow breath, Seer's top
            hand kept off the subtitle. Both read it and answered "ready"; neither had a pending request.
Blocked on: nothing.

## h00 · web/landing · the robot's entrance is SIMULATED, shared by two bodies; and the jointed one can point
Files:      web/landing/robot.js (primitives Bracket Bot: 12 meshes, 2,548 triangles, painterly v2; makePopUp / poseRig /
            poseRigid / stageEntrance / pointAt / pointBeat), robot-splat.js (the scan, moved rigidly), robot-rollin.js
            (the replaced roll-in, parked), styles2.js + textures/watercolor_normal.png (verbatim copies),
            dev-robot.html, tools/dev/robot.mjs, tools/dev/robot-in-scene.mjs, HANDOFF-ROBOT.md; splat.js (canonical
            frame: scale only, facing checked not corrected), dev-splat.html (static raw viewer). scene.js, index.html,
            robotpop.js, roommap.js and all copy untouched.
Verified:   `node tools/dev/robot.mjs` — lands +6.3 deg, counter-leans -9.0 / +4.3 / -1.5 / +1.4, still in 1.7 s, base
            darts 25 cm and returns, wheel slip 1e-16 m, no jump between 240 Hz samples, 24 draw calls, 0 console messages.
            `node tools/dev/robot-in-scene.mjs <out> splat|primitives` in the REAL page: 60 fps before and after, worst
            frame as it appears 18-20 ms (86 ms without the shader warm-up), waits for the title's own `landed` flags.
            `node tools/dev/splat.mjs` after repairing dev-splat.html: loads, 0 console errors, 0 external requests,
            facing check 0.2 deg off +Z. Pointing beat captured at six frozen times: head first, arm on target, glance back.
Blocked on: the landing owner for WHERE the caretaker beat lives and what publishes the target (asked). Which body is the
            hero is the user's call: the last two directions disagree (primitives approved, then "the landing robot is
            the scan"); both are one MODULES line.
Surprise:   The balance catch was cheaper to simulate than to fake: a cart-pole with placed poles gives the overshoot, the
            decaying counter-leans and the base darting under the mast for free, and a first guess at "realistic" motor
            limits made it fall over, exactly like the real thing. A splat can share all of it (a rigid body needs only
            y, x and a lean about the axle) but it can never point, so the caretaker story needs the jointed robot.
            Also: I broke dev-splat.html with a string replace that matched a comment instead of the code; the repaired
            page and the tool that would have caught it are both in.

## h21 · web · the roommate dashboard skeleton: the room-clean CI badge, "where are my keys?" → Point → live job state, the copy
Files:      web/landing/dash.{js,css}, web/landing/graph.js (lead copy), web/roommate_api.py (new),
            web/tests/test_roommate_api.py (new), web/server.py (router list), web/PAGES.md, handoff files (names removed)
Verified:   On http://localhost:8000/?info, headless Chrome, no JS errors, no sideways scroll:
            (1) CI BADGE. "Is the room at main?" opens with `room-clean  passing — nothing to commit, working tree
            clean — the room is at main` in green; red is `failing — N things have drifted from main. A mess gets put
            back; a decision goes through a pull request.` with the drift list in git-status words (modified: /
            untracked: / deleted:). It prefers GET /api/room/ci and falls back to /api/status; SSE `status`,
            `room_state` and `capture` refresh it. The page now opens ONE EventSource, shared with the graph.
            (2) FIRST DEMO BEAT. Typing "where are my keys" → keys_7c2e (HERE NOW, zones/shelf, BM25 ✓ VECTOR ✓ #1) →
            [Point at it] → POST /api/object-life/keys_7c2e/point → `job_… · queued (no executor connected) — planned;
            no robot is connected to this server yet, so nothing moved`, then live from SSE `job` events (and
            GET /api/jobs/{id} for stored jobs). The button arms after 600 ms, ignores double-clicks, re-arms on a
            terminal state (for "5 in a row"), and is disabled WITH the reason when the object is absent or has no pose.
            (3) COPY: section heads "Is the room at main?" / "Where did I leave it?" / "Who moved what, and when";
            the search's idle text is the roommate's, the hybrid-retriever sentence kept underneath.
            TASK-web #1: web/roommate_api.py — /api/room/ci (git's own working tree; `since` and `heartbeat` are null
            until the watch loop reports, and the answer says so) and /api/blame/{id} (the commit that last changed
            the object, that commit's own from → to, its capture; mug_a1b2 → "afternoon: mug moved…", 0.19 m,
            cap_0005; tool_4f2a → removed in "the bench, tidied") are REAL. /api/chores and /api/prs answer [] with
            X-Roommate-Backend: not_connected; POST /api/prs, approve and /api/nav/snapshot answer 503 not_connected
            with what is missing. Nothing invents a chore, a PR or a robot. web/tests: 121 passed.
            For the 3D roommate (landing visuals session): window events gitrl:point / gitrl:job / gitrl:room-state in
            the room frame, and an empty #roommate-stage under the search box — verified firing.
Blocked on: a restart of :8000 for roommate_api (the badge already works through /api/status); roomctl's watch loop,
            PR store and the nav bridge for tasks 3–6; the dispatcher to the edge for a job that actually runs.
Surprise:   The legend's SYNTHETIC chip had been rendering as a blank white block (ink text on an ink chip) — fixed.

## h00 · elastic · caretaker plan: keys beat rock-solid, §10 shapes dry-run clean, blame + time travel, pitch fixed
Files:      elastic/setup_elastic.py (rerank model), elastic/queries.py (moved_at, commit_at strictly_before),
            elastic/tests/test_new_shapes_live.py (new), elastic/tests/test_queries.py, elastic/NOTES.md,
            elastic/README.md, docs/07-prizes.md, docs/11-elastic.md
Verified:   KEYS: 12/12 phrasings top-1 keys_7c2e on main and on all history; 5/5 repeats of each demo
            phrasing. Before: "where did I leave my keys" won by 0.041. Switched endpoint jina-rerank to
            jina-reranker-v3.5 after a side-by-side on the same candidates. Every keys phrasing now wins by
            > 0.3; scissors 5/5; `demo_hybrid.py mug --save` re-run and saved: cup_7e21 BM25 MISS,
            dense #2, final #2.
            §10 DRY RUN (isolated test-shapes- indices, deleted after): 5 new event types, bb_map
            observations and nav_x/y/yaw telemetry all accepted; commit_at ignores the new event types. A
            field outside §10 (pr_number) is rejected whole.
            BLAME: moved_at(mug_a1b2, main) → 1a668ec0 "afternoon: mug moved…", cap_0005, from (0.42, 0.18,
            yaw 15) to (0.61, 0.18, yaw 40), plus the capture doc and each camera's view.
            TIME TRAVEL: commit_at(00:30Z, main) → b3691ead "the bench, tidied"; at exactly 1a668ec0's
            timestamp, strictly_before=True → b3691ead.
            Full live suite 153 passed.
            PITCH: "Agent Builder" claims removed from docs/07 and docs/11 (docs/14 had none) and from
            elastic/README.md; the line is now "an agent whose last tool call moves a real object".
Blocked on: key rotation (Kibana step); master wiring `restore --before` (branch + strictly_before=True).
Surprise:   The keys beat passed every check yet was one description change from failing: a 0.04
            reranker margin. Checking the margin, not just the rank, is what caught it. Also: any
            §10 producer that adds a field (a PR number, a chore URL) loses the whole doc to the
            strict mapping. The dry run pins that.

## h13 · robot · depth from the robot: MEASURED, then built — cam0=bbos:camera.rect (colour + mm depth + K, one frame)
Files:      robot/bbos.py, robot/capture.py (per-camera max_frame_age_s, camera_meta), robot/probe_bbos.py (fixed),
            tests/test_robot_bbos.py (+7), robot/RUNBOOK.md §6, docs/16 §2.8
Verified:   read-only on the robot. Units: camera z of bbos's points / depth = 0.00100038 over 60,697 px. K two ways:
            yaml P1 x 0.4 = fx 131.205 ppx 229.071 ppy 200.752; fitted from bbos's points = 131.21 / 229.07 / 200.75,
            0.02 px residual. Then THIS code, in memory, under ~/gitspace/.venv on the robot: 3 latches, colour 512x384
            36 KB + depth png16 60 KB, encode 15 ms, depth median ~960 mm, coverage 0.986 (confident 0.39), K read
            live from bbos's files. Looked at one frame: colours right (then deleted it — it had people in it).
            tests/ green; audit 19 ok. NOT pushed to the robot (LINK's push); current deployment unchanged.
Blocked on: a decision, not code: obs.capture_quality's coverage > 0.60 was set for dense SGBM; bbos's confident
            depth is ~0.31-0.39 of the image by design. Default here gates on the sensor's raw coverage (0.986).
Surprise:   My own probe LIED on first contact: it printed "=> MILLIMETRES … MEASURED" for a ratio of 4.5e-5, because
            it picked the nearest unit label without checking the fit — camera.points turned out to be in the BASE
            frame (z = height above the floor), so its z is not depth at all. A measuring tool that says "measured"
            when the check failed is worse than none; it now says INCONSISTENT, and the real check goes through
            bbos's camera_to_base matrix per pixel. Also: a healthy camera.rect frame is 160-207 ms old at latch, so
            the rig's 250 ms "camera stalled" rule — right for 30 Hz cameras — would have fired on it at random.
            And Gate 1's motion steps (start nav, /navigate, the arm) were asked of this session by another one:
            declined — a balancing robot and an arm near people need the user's own word and a person at the robot.

## h22 · web · the roommate panel on /robot made compact and calm; the point job's SSE event carries where it points
Files:      web/pages/robot.html (the chat section + the tab label), web/pages/room-chat.{js,css}, web/object_api.py
Verified:   Daniel's feedback: the Room Agent panel was "WAY too big" and its subtitle named an internal bridge. In
            Chrome on localhost:8000/robot, desktop 1440×900 and phone 390×844, no JS errors, no sideways scroll:
            · size: the empty panel went from 392×748 to 348×331 px; header 96 → 50 px (one row: ROOMMATE + the status
              line, +, ×); the composer 103 → 40 px — a ONE-LINE input that grows with what is typed up to five lines
              and shrinks back after a send, with the send button inside it; the conversation now gets the rest and
              the panel is only as tall as what is in it (up to the viewport). Conversations / Export / Delete folded
              into one quiet "Conversations ▾" row at the bottom. On a phone it is a bottom sheet, 62 % of the screen.
            · status line says what is ARMED, in plain words: "caretaker · parsed here · the robot runs via Housebot
              Edge" when GET /api/housebot reports enabled, else "caretaker · plans only, robot not connected" (it
              wraps rather than truncating — "robot not connected" must never be cut off); a stand-in parser says so.
              No internal names anywhere in the visible UI ("Via andrew:jsonl / middleware" → "understood here · a
              plan, nothing moved"); the raw response stays available, collapsed.
            · voice: "I remember where everything belongs." / chips "Is the room clean?" "What changed?" "Who moved
              what?" (they send now, instead of only filling the box) / a status answer reads "Nothing to commit,
              working tree clean — the room is at main." / a plan reads "Here is what “restore study” would take: 3
              things to move. Nothing has moved." The tab is "Roommate".
            · the SSE `job` event for a planned point now carries target_pose, zone and frame (room frame), so a page
              that did not start the job — the 3D roommate — can mirror it. web/tests: 134 passed.
Blocked on: a restart of :8000 for the SSE field and roommate_api; `housebot` is in the router list but not mounted on
            the running process yet, so the status line reads "plans only" until then — which is also the truth.

## h09 · cloud · the caretaker bridge is ours end to end, and "Where are my keys?" ran 5× through Andrew's real edge
Files:      bridge/intent.schema.json, bridge/intents.py, bridge/caretaker.py, web/housebot.py (all new);
            bridge/agent_api.py (caretaker path), bridge/andrew.py (our grammar is the default; his parsers are
            test doubles), bridge/contract.py (intent_unavailable is an outage), web/object_api.py (build_point,
            Idempotency-Key, dispatch), web/jobs.py (GET falls back to dispatched jobs), web/server.py ("housebot");
            tests: bridge/test_intents.py (39), web/tests/test_housebot.py (15); docs/31 §3c, .env.example
Verified:   bridge 64 · web 135 · telemetry 40 · roomctl 575 · agent 3 · backup 2, all green. Localhost, real room +
            real Elastic, a throwaway web on :8099 → Andrew's REAL edge (run_edge_api.py --mock, 9582081) on :8781:
            "Where are my keys?" ×5 → grammar find/keys → Elastic hybrid search keys_7c2e (score 1.454, shelf,
            x 0.62 y 0.78 z 0.91 yaw 140) → point job → his CaretakerService → POINT_AT_OBJECT → succeeded, 5/5,
            0.5–0.8 s each. The fake-edge tests cover failed, retryable, 401, an unreachable edge (3 tries →
            undelivered), and no answer (→ unknown, sent exactly once).
Blocked on: Andrew's edge on the LAN + HOUSEBOT_EDGE_URL/TOKEN in .env (master); `point`/`move` on
            WEB_ALLOWED_COMMANDS (the user's switch); POINT_AT_OBJECT behind Ryan/Sarah's RobotAdapter (the real 5×).
Surprise:   The first run proved the failure path by accident: a shell `exec` placed after `env` meant his edge
            never started, and all six jobs came back `undelivered` after 3 refused connections each. Nothing
            hung and nothing was sent twice. That is exactly what the robot must never
            get wrong. And Elastic answered "my keys" with keys_7c2e at the very pose the plan's §12 example uses:
            the hybrid search is the part of this chain that already works on real data.

## h13 · robot · ROBOT_ALLOW — a peer allowlist, because the robot's camera was open to the whole venue wifi
Files:      robot/server.py (PeerAllowList, pure ASGI), robot/config.py, tests/test_robot_server.py (+3), docs/16 §2.7 + §8, robot/RUNBOOK.md §4
Verified:   real socket, server on 0.0.0.0 with ROBOT_ALLOW=127.0.0.1: via 127.0.0.1 /camera → 200; the same server via
            the tailnet address (a genuinely different TCP peer) → 403 on /camera, /capture, /healthz, and still 403
            with a spoofed X-Forwarded-For: 127.0.0.1. Tests: WebSockets closed 1008 before accept; IPv4-mapped IPv6
            peer accepted; a typo in the list raises instead of meaning "nobody" or "everybody". NOT yet on the robot.
Blocked on: LINK setting ROBOT_ALLOW in the robot's .env and pushing; the real fix (bind to the tailnet) is blocked on
            the two-tailnets account share.
Surprise:   We spent the day making sure a rejected capture ships no pixels, and the whole time GET /camera/cam0.jpg
            — added for a live view — answered 200 to every phone on the hackathon wifi, with people in frame. The
            contract said "no auth, private network"; the network stopped being private the moment the robot joined
            the venue wifi, and nothing in the code noticed.

## h00 · web/landing · the caretaker beat is on the dashboard's stage: "where are my keys" -> it drives over and points
Files:      web/landing/roommate.js (mounts into the dashboard's #roommate-stage, follows gitrl:point / gitrl:job /
            gitrl:room-state), dev-roommate.html, tools/dev/roommate.mjs, textures/watercolor_normal-1024.webp (0.2 MB
            instead of 7 MB), robot.js (pointAt, pointBeat, keyFor, strokeMap and keyDir options), dev-splat.html
            (repaired; stage view centred), splat.js (canonical asset: scale only, facing checked not corrected),
            HANDOFF-ROBOT.md. No dashboard, hero or server file edited.
Verified:   `node tools/dev/roommate.mjs <out> live` against http://127.0.0.1:8000/?info#search: mounts, un-hides the
            container, the dashboard's REAL room-state event drove the caption, a point at keys_7c2e (0.62, 0.78, 0.91)
            reads "planned · pointing at keys · shelf", loop stops off screen; 0 console errors or warnings, no request
            leaves localhost. `node tools/dev/roommate.mjs <out>`: near object -> turns only; far -> drives and stops
            0.62 m short; "running" then "succeeded" lowers the arm; reduced motion -> a still with the arm up.
            `node tools/dev/splat.mjs` after the repair: 34,911 gaussians, facing check 0.2 deg, clean.
Blocked on: one script tag in index.html (the dashboard owner's file; asked): <script type="module" src="./roommate.js">.
Surprise:   A pointing robot has a camera problem before it has an animation problem: whenever the object lies between
            it and the lens, the arm is a foreshortened stub. The fix was to move the CAMERA to a side view per gesture,
            and my first version of that picked the better-lit side and let a clamp pull it back out of profile; judging
            the two candidates AFTER clamping fixed it. Also: my own stage captioned "room at main" before any event had
            said so. An illustration that speaks first is a small lie; it now says nothing until the badge does.


## Telemetry camera diagnostics and edge-arm layout
Files: web/pages/seer/{decor.js,camera-diagnostics.js,verify-stage.mjs,verify-camera-diagnostics.mjs}, web/pages/telemetry-robot.js.
Changed: upper decorative hands now enter at separate screen edges, clear of the character and title. Camera diagnostics reuse existing local polls and expose both frame ages, delivery/target rate, frame counts, preview cache/read counts, frame and event drops, unavailable-camera reasons, pending/open watcher conditions, and all returned recent failure/recovery records. Missing measurements stay unreported; failed status requests are visible; restart/reset counters cannot produce negative frame rates. No additional camera reader or telemetry sender was added.
Verified: local desktop/mobile visual captures; entrance, skip, reduced motion, and interaction fixtures passed. Diagnostic fixtures cover unavailable cameras, missing values, dry-run reporting, pending/recovered conditions and HTML-like error text.
Blocked on: full camera coverage is not exposed by the live-preview API: it serves one configured colour camera, not depth/calibration for every sensor. Complete error history and delivery confirmation remain in Sentry; the watcher endpoint only returns a recent bounded list and reporting configuration. The external watcher probes camera availability at startup through health state, but catches preview-read failures without filing them; detecting runtime camera stalls needs the watcher owner to instrument that path and verify fault/recovery on hardware. No live fault injection, remote checks, service restarts, deployment, or commits were performed here.

## h13 · perception/pointcloud · bb_source: BB's voxel map → candidates → the same commit chain; G2 across passes green on bbsim
Files:      perception/bb_source.py (new): Candidate, candidates, fresh_blocks, block_of, fresh_fn, visibility_grid,
            visible, eye_room, scan_into_bb, plus Fit/MapObject (a box fitted on BB's own lattice).
            perception/costmap.py (+Costmap.from_bb_grid). perception/tests/test_bb_source.py (new, 21 tests).
Verified:   test_bb_source 21/21. perception/tests: 291 passed, 12 skipped. Repo tests touching frames, bb_nav,
            base_pose, executor, cli and pr: 431 passed, 1 failed. The failure is test_bbsim's slow-reader test, a
            websocket handshake returning 400 inside bbsim, which is being built right now; it doesn't touch this
            code.
            Accuracy on synthetic 1.5 cm voxels at 16 lattice angles × 2 scenes (plan 04's bar is ≤1 cm / ≤1.5 cm / ±5°):
            centroid ≤ 6.9 mm (xy) and ≤ 7.5 mm (z), sides ≤ 10.6 mm, yaw ±5° for everything ≥ 4 cells across.
            The 8×4 cm keys reach 10° at the worst lattice angle (mean 3°): a resolution limit, stated in the test.
            Live, loopback bbsim (--T 30°,1.2,-0.8) through roomctl.bb_nav.BBNav: 11/11 objects, centres ≤ 10 mm,
            yaw ≤ 5°; 5 passes, `git status` clean after every one (0.13–0.34 s per pass). /sim/move of the cup by
            12 cm → exactly 1 `moved`; moved back → clean.
            bbsim scenario 3 (test): keys gone from the map AND a board in the way → `unobserved`, file byte-identical,
            3 passes. Board gone, block fresh → removed after MISSES_TO_REMOVE. Keys gone but block stale → never a miss.
Blocked on: labels. Candidates are class "unknown" until perception-segment #1 projects masks onto them, and
            scan_into_bb raises on segmenter/describe until then. EYE_H 0.95 and OVERSHOOT 0.75 need measuring on the
            real robot and desk.
Surprise:   1) cluster.Instance.box is the wrong model for 1.5 cm cells. Its min-area rectangle flipped the mug by up
            to 40° depending on BB's lattice angle, and every side read +12..18 mm once the object sat at an angle to
            the lattice. Fit searches for the angle whose rasterised rectangle reproduces the occupied cells. The
            overshoot also depends on face length: a 3 cm face has too few cells to overshoot. 2) scene_gen.scene_cloud
            draws AXIS-ALIGNED boxes, which is fine for a costmap but has no yaw to recover (bbsim already samples
            oriented ones). 3) VoxelMirror.points() returns index × res (cell corners); bb_source detects the offset
            instead of assuming centres. 4) At 3 cm, the surface an object stands on clips a grazing ray: the
            visibility grid drops each zone's surface layer, with a two-cell margin because rotated rim cells land
            outside the zone.

## h23 · web · camera frames are for this laptop only; the caretaker's answers in both consoles; the system drawn as a commit graph
Files:      web/localonly.py (new), web/server.py (LandingFiles: /live/* local-only + no-store; 403 = forbidden),
            web/telemetry_api.py, web/camera_ingest.py (privacy note), web/tests/test_standalone_pages.py,
            web/landing/dash.js (dispatched point jobs), web/landing/graph.js, web/landing/index.html (roommate.js),
            web/pages/room-chat.js, web/pages/robot.html, web/pages/room-connections.{js,css}
Verified:   PRIVACY (first, as asked). web/landing/live/ is what the camera receiver wrote: real frames of a real room.
            The static mount now serves /live/* only to a loopback peer carrying NO forwarding header — a tunnel also
            arrives from 127.0.0.1 — else 403 {"error":"forbidden"}, and with Cache-Control: no-store. The rule lives
            once, in web/localonly.py (the robot-view router's header list plus Vercel's), and the event inlet and the
            billed Seer start use it too. Tested: loopback ok; another host, and loopback with x-forwarded-for /
            cf-connecting-ip / forwarded / x-vercel-forwarded-for / tailscale-user-login all 403, `LIVE/` too;
            everything else under landing/ stays public. Nothing of web's reads /live/, so no page needs a fallback.
            THE CARETAKER PATH in both consoles (docs/31 §3c: kinds job / jobs / proposal, reads status / blame). The
            roommate panel answers in words — "Found it: keys — on the shelf. I would go over and point at it … This
            is only the plan: no robot is connected right now. Nothing moved." · "Moving it to the shelf changes where
            it BELONGS. That is a decision, not a mess — it goes through a pull request…" · "mug was last moved 19 cm
            in 1a668ec — “afternoon: mug moved…”" · "Nothing to tidy: every object is where it belongs." — and follows a
            DISPATCHED job through GET /api/jobs/{id}. The commit graph's console draws the same, and its route rail
            now reads: this graph → router → caretaker · parser (or the language layer) → planner → Housebot Edge →
            robot. No internal service names in visible text. The Point button words every terminal state, including
            "sent, but no answer came back: it may or may not have moved", and only polls a job that was dispatched
            (a planned one is not stored: that was two 404s per click).
            THE 3D ROOMMATE (landing visuals' roommate.js) is loaded on /?info: "where are my keys" → Point → the
            Bracket Bot turns and points at the keys' real height, captioned "planned · pointing at keys · shelf";
            0 errors, no request leaves localhost.
            CONNECTIONS on /robot, as asked: a commit graph of the system. `main · room.git` runs the height; a COMMAND
            branch (you → caretaker parser → Housebot Edge → robot adapter :8765 → Bracket Bot nav) merges back as
            "verified by rescan"; a PERCEPTION branch (robot camera :8080 → scan pipeline) merges as "commit"; MEMORY
            (Elasticsearch, Sentry) hangs off main. Each node is green only with proof from a real endpoint
            (/api/room/ci, /api/agent/bridge, /api/housebot, /api/nav/snapshot, /api/robot/view/status, /api/status,
            /api/health, /api/config), amber = configured or stale, grey = not wired, red = failing; the reason shows on
            hover / focus / tap and it opens on whatever most needs saying. Live now: 5 proven · 3 not proven · 4 not
            wired (the edge, adapter, nav and the rescan merge are grey; the camera is amber — 434 frames, idle). Edges
            take the worse state of their ends. Chrome 1440 and 390 px: no errors, no sideways scroll. Refreshes every
            15 s while visible. web/tests: 137 passed.
Blocked on: a restart of :8000 for the /live/ rule, the SSE target_pose and localonly (the deployment lead's).

Follow-up: the runtime preview reporting gap above is fixed in scripts/robot_sentry_watch.py. HTTP failures, malformed/stale pictures and read exceptions now enter the existing camera_unavailable debounce and recovery path; healthy preview sampling frequency is unchanged. Three isolated tests cover sustained failure, single-report debounce, recovery, timeouts and stale frames. The 32 existing Sentry client and recovery tests also pass. Remaining blocker: the running watcher must be reloaded by integration and the actual camera failure/recovery verified; this session did not restart processes or inject hardware faults. The preview probe still covers the first available camera; full sensor coverage requires per-camera probing.

## h10 · cloud · the room-clean badge behind one switch, nav failures as grouped issues, the SLAM path as telemetry
Files:      telemetry/room_clean.py (new), robot_sentry.py (IssueMirror through it), web/roommate_api.py (/api/room/ci
            reads the verdict), roomctl/bb_nav.py (report() + breadcrumbs + map_reset), obs.py (robot_failure
            level/context/fingerprint, breadcrumb()), telemetry/hub.py (NavTap, Hub.offer, --bb); tests:
            telemetry/test_room_clean.py (6), tests/test_nav_sentry.py (6), telemetry/test_nav_tap.py (4),
            web/tests/test_roommate_api.py (+1); docs/29 (server_name `robot`, crons and nav as built)
Verified:   bridge 64 · web 137 · telemetry 49 · roomctl/robot 618 · agent 3 · backup 2, all green (one bbsim
            timing test excluded: tests/test_bbsim.py::test_a_slow_reader…, the sim's own in-progress work,
            which never touches bb_nav). No Sentry calls: every test records into a fake.
Blocked on: ROOM_CLEAN_CRON=1 waits on the user deleting `watch-loop` in Sentry (the one cron seat).
            roomctl/watch.py (the debounced RoomState feeder) isn't built; IssueMirror's git status feeds it
            until then. NavTap is in the room frame only once a registration provider exists.
Surprise:   IssueMirror had been ready to send `room-clean` check-ins whenever `robot_sentry.py mirror` ran,
            with no switch. That would have tried to take the seat `watch-loop` still holds. And nav_short's
            message carries the distance, so without a fingerprint every trip that fell short would
            have opened its own issue.

## h13 · robot · "no Sentry spans from the robot": NOT the DSN — checked read-only, then proved the HTTP path
Files:      tests/test_robot_server.py (+4: real sentry_sdk + FastAPI integration into memory), robot/server.py
            (/healthz.sentry: live + rate_limited), docs/16 §2.1b
Verified:   on the robot, counts only, no values printed: SENTRY_DSN present, non-empty, URL-shaped; rate 1.0; env
            htn2026; ingest host resolves and answers HTTPS from the venue wifi; clocks agree to the second; 11 captures
            since the server started. Locally, REAL sdk: POST /capture -> ONE transaction "/capture" (http.server,
            server_name robot) holding robot.capture/latch/retrieve/capture_gate spans made in the worker thread, tag
            capture_id, measurements skew_ms + tilt_rate_max; an incoming sentry-trace is CONTINUED (same trace_id,
            parent_span_id) and the id is on the capture body; a "-0" probe makes no transaction. Nothing written to the robot.
Blocked on: someone with Sentry access checking Stats -> transactions for 429s / quota, and searching SPANS not transactions.
Surprise:   The suggested fix was to copy the DSN onto the robot and restart it. The DSN was already there: the "proof"
            it was missing was that it is absent from the process's initial environment — which is true of every
            variable python-dotenv loads, and proves nothing. And `robot.capture` was being looked for as a
            transaction; under the FastAPI integration it is a span inside the `/capture` transaction.

## Telemetry: distinct camera and spatial views
Files: web/pages/telemetry-robot.js; web/pages/seer/spatial-{telemetry,viewer,theme}.js; web/pages/seer/verify-spatial.mjs.
Changed: six view modes in the telemetry panel: live camera, per-camera point cloud, stored voxels, recorded room map, captured camera gallery, and object/source data. Live camera has separate left-lens, right-lens and stereo controls. Camera stream stops outside its view. One lazy 3D renderer handles point clouds, instanced voxels and object geometry; renders on interaction, pauses when hidden, and exposes orbit/zoom/fit/top controls. Existing telemetry signals, diagnostics and incident history remain below the views. Data refresh is bounded and only runs for the visible spatial panel; missing data can be retried. Camera clouds remain separate because no registration between them is provided. Manifest paths are restricted to local capture assets.
Verified: Chrome desktop 1440 and mobile 390: each mode, both lens selections, camera switching, missing-manifest/retry state, local asset validation, no horizontal overflow, no page errors. Inspected point-cloud, voxel and room-map screenshots. Source APIs read on localhost only; no commands, new captures, service restarts, commits or deployments.
Blockers: localhost /live/latest.json currently holds cap_0004 from sender_mode=sim, captured 2026-09-19T06:46:41.520Z, with assumed intrinsics (74,892 points per camera). It is explicitly labelled simulated and approximate; a current hardware depth capture must be published to replace it. /api/voxels returns 745 indexed cells with unknown sensor provenance. /api/nav/snapshot returns 503 not_connected, so no live robot pose or navigation grid is fabricated. These backend gaps prevent calling the stored views a current live room reconstruction.

## h17 · perception/segment · roommate tasks 1-4 on my side; a difference segmenter that says NOTHING about an untouched real room
Files:      perception/difference.py (new): difference(baseline, current) -> Change(appeared, gone), plus a CLI
            over two recording dirs. perception/tests/test_difference.py (new, 16). segment.py
            (+label_map_objects, MIN_CROP_PX/SEEN). describe.py (+describe_added). merge.py (object_fields
            + vlm_model). tests/test_boundaries.py (+3). fake/README.md (room-objects contract lists vlm_model,
            as the mapping already did). docs/15 (Approach C, as built). docs/10 (the recording's floor tilt).
Roommate:   #1 labels: label_candidates, then label_map_objects puts label, score and a croppable mask on each
               map object. #2 miss rule: a miss only if its block is fresh AND in sight, tests first; bb_source
               already calls it. #3 words for NEW objects: describe_added runs the VLM on ADDED views only, so
               a quiet room costs no call. object_fields now carries vlm_model, which elastic's META_FIELDS
               expected: before this, real words reached room-objects with a null model. #4 observations as
               camera "bb_map", with occluded from the raycast. Wiring #1 and #3 into scan_into_bb needs a
               frame provider (image, K, room->camera), which is pointcloud's; the call site was handed over.
Difference: LINK acceptance (a): cap_0004 vs cap_0005 (hallway, untouched, 5 s apart) gives 0 appeared and 0 gone,
            both ways. The rule is free space along the ray: nearer than EVERYTHING the baseline saw in a 5x5
            window, by 5 cm + 3 cm/m^2 z^2.
            Crops, all measured on that pair:
              - a 38 deg cone: every untouched blob over 40 cm^2 sits past 41 deg, at the fisheye rim;
              - 1.7 m range;
              - 0.35 m for the robot itself.
            Blobs grow down the object's face (within 3 cm of it) and merge where their grown regions
            meet. The area floor is 100 cm^2 facing the camera; the largest untouched group is 16 cm^2.
            All of this is on the recordings' measured mount, 38.1 deg / 1.59 m; it was re-tuned after
            the first pass had used the old 33 deg one.
            The test renderer's noise is calibrated to the pair: median |dz| 2.4 cm at 1.4-2 m, neighbour
            correlation 0.9. Over 30 noise draws:
              | case                  | found |
              | 20 cm box at 1 m      | 30/30 |
              | moved box             | 29/30 |
              | 20 cm box at 1.5 m    | 22/30 |
              | 15 cm box at 1 m      | 0/30  |
            On the real pair, a ray-cast 20 cm box on the measured floor is exactly 1 instance at 3 positions.
            0.15 s per pair. (b), a real box 1 m ahead, can't be staged: the robot doesn't hold its heading
            between captures (LINK measured 3 rotations; pose_source none), so there's no same-view pair.
Verified:   perception 310 passed, 12 skipped, 1 xfailed; G2 (tests/test_idempotent_scan.py) 16 passed, 8 skipped;
            elastic 153 passed. Audit 18 ok / 1 warn / 0 FAIL. The warn is telemetry/ and obs.py newer than
            docs/23 and docs/18, from the h10 cloud work, not this track.
Blocked on: (b) needs either a relative pose between captures (a 3-DoF registration on top of LINK's
            per-capture levelling, offered) or the bbos SLAM map. scan_into_bb has labels and words wired
            (pointcloud); feeding its camera_frame() the head image is the robot link's (LINK).
Surprise:   1) The real stereo error is a smooth local warp (adjacent pixels correlate at 0.94), not
            per-pixel speckle. A renderer with iid noise shattered boxes that the real sensor wouldn't, so the
            test noise is now fitted to the real pair, correlation length included. 2) Every lower tau tried took
            the real pair's largest untouched blob from 32 to 190+ cm^2: what sensitivity remains is limited by
            the sensor, and a per-capture multi-frame median is the next lever. 3) I measured a floor rising
            6 cm/m in LINK's recording. That was the old 33 deg mount: the recordings had been recalibrated
            to 38.1 deg / 1.59 m at 13:05, after my cache was built. Re-measured, it's flat to ~1 cm, and
            docs/10 is marked resolved.

## h13 · robot · the edge's robot adapter (:8765): POINT_AT_OBJECT against a simulated robot, one conversion, hardware refused
Files:      robot/adapter.py, robot/frames.py (thin: re-exports roomctl/frames.py + the registration's provenance),
            tests/test_robot_adapter.py, robot/RUNBOOK.md §6b, robot/README.md, docs/16 §8
Verified:   the edge's OWN client (gitirl @ 9582081, HTTPRobotAdapter) against `python -m robot.adapter --sim` on
            loopback: POINT_AT_OBJECT -> success; observe() -> robot_pose_room (1.5008, 0.3328) = 0.600 m from the
            object at (2,0), heading -33.69° = atan2(-1, 1.5); MOVE_OBJECT -> failed "not implemented"; a target in
            another coordinate_frame -> failed; wrong token -> his RobotAPIError 401. 8 adapter tests + roomctl's 38
            frame goldens green together; sentry-trace continued with adapter.action/transform/navigate/point spans.
Blocked on: Gate 1, by people at the robot: BB nav up, one commanded arm motion proven, T_bb<-room MEASURED
            (ROBOT_REGISTRATION). And push_to_pi.sh shipping roomctl/frames.py beside robot/.
Surprise:   I wrote a second room<->BB conversion an hour after the plan said "two estimates of one transform would be
            the bug" — roomctl/frames.py had landed that morning and I had not looked. Both derived h = theta + phi -
            pi/2 independently, which is reassuring and irrelevant: the second copy is deleted, robot/ re-exports the
            first, and a test now fails if sin/cos ever appears under robot/. Also a test of mine "failed" on -0.588 rad
            where I expected 0: under a registration that is not the identity, BB's origin is not the room's, so the
            robot does not start at the anchor. The code was right; the intuition was the identity's.

## h11 · cloud · Sentry survives venue wifi; robot failures also land in Elasticsearch; restore is executable; plan-only is machine-readable
Files:      obs.py (keep_alive, transport_queue_size 1000, robot_failure → room-events via a spool, trace_headers,
            transaction(parent=), robot role never profiled by default), web/housebot.py (continues the
            asker's trace; sentry-trace + baggage reach the edge), web/jobs.py (plan_only, why_not_code),
            web/graph_api.py (refusals say how to enable; /api/commands `jobs` discovery), .env (restore on
            WEB_ALLOWED_COMMANDS, as the user asked), .env.example, ANDREW-HANDOFF.md (executable vs plan-only),
            telemetry/test_obs_durable.py (7, subprocess against loopback fakes), web/tests/test_jobs.py (+3)
Verified:   bridge 64 · web 139 · telemetry 56 · roomctl/robot 625 · agent 3 · backup 2 · audit 18 ok, 0 FAIL.
            Live on a throwaway :8099: restore study → job_aba556d557b65301 executable; revert HEAD →
            job_433ea50059117524 plan_only/why_not_code plan_only; checkout study → job_0e7bd69e259e2ec6
            executable. His robot_actions_from_daniel_job (9582081) on each: MOVE_OBJECT(mug_a1b2) and
            unsupported removed/added. Sentry stats_v2 baseline (2 h): network_error drops 7 errors, 68
            transactions, 525 spans, 42 profiles, ~656 KB logs, concentrated ~1 h earlier.
Blocked on: a restart of every obs process for keep_alive to take effect (web :8000, the hub, roomctl, the
            robot); `restore` on the GCP mirror's allow-list (master); the stats re-check an hour after that.
Surprise:   Andrew's translator reads the git-level preview `ops`, not the plan, so it would turn a plan-only
            revert into a MOVE_OBJECT. `executable: false` was right, but only an edge that reads it is safe.
            And 5,006 profiles were discarded client-side as `insufficient_data` in 2 h: profiling work that
            never arrived. That's the robot's CPU on the Jetson, now off there by default.

## Seer entrance: colour, geometry and pacing
Files: web/pages/seer/{intro-effects.js,intro-bugs.js,lettering.js,seer.js,verify-offline.mjs}.
Changed: 24 instanced rings/prisms/shards, three orbit arcs and a restrained arrival/catch ripple surround the character during its entrance; bug targets and bursts use pink, coral and violet. Extruded SENTRY letters now use a saturated pink-to-coral vertex palette with shaded sides, avoiding the previous lighting washout. Entrance pacing slowed from 1.8× to 1.35×; early skip now advances the entrance timeline fully so lettering/effects cannot linger over the dashboard. All new geometry hides when the entrance ends, under reduced motion, or while the scene is paused.
Verified: Chrome desktop/mobile screenshots inspected; entrance, skip, reduced-motion, dashboard visibility and no-overflow checks pass. No new asset service, server restart, deployment or commit performed.
Blocked on: nothing for this visual change.

## h13 · perception/pointcloud · bb_source gets names and words from the robot's frame; the floor tilt is charted per capture
Files:      perception/bb_source.py (CameraFrame and camera_frame(): BB pose through frames, head mount through
            fuse.rect_to_world, inverted. scan_into_bb(frame=, segmenter=, describe=, vlm=) calls
            perception-02's segment.label_map_objects and describe.describe_added), perception/fuse.py (floor_fit;
            fuse() charts floor_tilt_ahead_deg / floor_tilt_side_deg / floor_z_at_robot), tests: test_bb_source
            (+2), test_fuse (+3), test_trace (+1 assert), docs/10 (reply under perception-02's floor entry).
Verified:   perception/tests 315 passed, 12 skipped. With a frame, the one object the stub segmenter names is
            committed as lamp_…, the rest as unknown_…. Words and vlm_model reach the staged scan metadata. A quiet
            second pass makes 0 VLM calls and git status stays clean. On the real hallway (cap_0004 / cap_0005,
            recalibrated mount 38.1° / 1.59 m): tilt −1.11° / −1.26°, roll 0.0°, +2.2 / +1.9 cm under the robot.
Blocked on: a source for the robot's frame on the BB path. camera_frame() is the provider, but someone has to hand
            it the head image, the /ws state nearest the shutter, and (f, cx, cy). EYE_H and the head camera's
            Mount are placeholders until measured.
Surprise:   docs/10's rising floor had already been fixed by the 13:05 recalibration (the entry quoted the old 33°
            mount). A least-squares plane through the whole floor still read 4.9° on the fixed data, because the
            densest strip, at the bottom edge of the rectified image, sets its tilt. Per-band medians agree
            frame to frame within 0.15°.

## h24 · web · one nav bar for the whole site, with the landing's Katie Roze wordmark
Files:      web/landing/brand-gitirl.svg (new), web/pages/sitenav.css, web/landing/index.html, web/pages/{capture,object,
            replay,telemetry,scene,live}.html, web/pages/{capture,object}.js (titles), web/pages/room-chat.css (the room
            page's brand), web/server.py (404 page), web/PAGES.md, web/API-FOR-PAGES.md
Verified:   Daniel: the top-left logo must be Katie Roze like the landing, the bar should look nicer and fit, and there
            were two navbars — drop the one with Status / Search / History and share the first.
            · WORDMARK. Katie Roze is a colour font with empty outlines, so it cannot be set as text. The logo is built
              from the SAME traced letter art as the 3D title (landing/title/gitirl.json): 2093 contour points simplified
              to 1038, one SVG path per letter (a single even-odd path punched holes wherever two brush strokes cross),
              13 KB, served from /brand-gitirl.svg. The bar shows it through a CSS mask on the link's ::before, so it takes
              currentColor (hover, focus) and the link's text stays for screen readers and for a browser without masks.
              The swashes hang below the caps, so the mark is nudged down 5 px: the CAPS are what centre on the bar.
            · ONE BAR. There were two designs: web's (brand · Status · Search · History · Telemetry · Room) and the room /
              live family's (brand · Overview · Room · Telemetry · Live). Every page now wears the second, through one
              stylesheet: /?info (its #dashnav carries the class; its private copy of the CSS and the section-observer
              script are gone), /capture, /object, /replay, /telemetry, /pages/scene.html, /live and the 404 page. The room
              page keeps its workspace toolbar and gets the same wordmark. Spelling is GITIRL everywhere, as the landing.
            · FIT. 60 px bar (52 on phones), wordmark 93×39 (72×30 on phones — it used to be hidden there, leaving no way
              home), links in a row that scrolls with a fade on narrow screens, the current page as an ink pill. The
              wordmark's left edge sits exactly on each page's own content edge (--nav-gutter): measured 100/100 px on the
              dashboard, 40/40 on capture, replay and object. Chrome at 1440 and 390 px: the mask is applied on every page,
              no sideways scroll, no errors. web/tests: 139 passed.
Blocked on: the 404 page's new links need the next restart of :8000; the public site needs landing/brand-gitirl.svg shipped.

### Telemetry hardware views — 2026-09-19 17:33 UTC
- Replaced simulated camera manifest with hardware capture cap_0022 from the robot. Captured cameras reject simulated/unverified sources.
- Read the robot's existing mapping.voxels + slam.pose without moving it or changing its services: 55,434 measured voxels, SLAM localized, visual odometry not lost. Point cloud, voxel and top-down tabs now use this hardware map (20,000 uniformly sampled measured cells); data tab exposes its metadata and geometric clusters. Removed the unverified stored-room fallback.
- Local snapshots: web/landing/live/latest.json and robot-map.json, explicitly timestamped. Refresh both with `.venv/bin/python web/pages/seer/refresh-hardware.py`. The web Refresh data control reloads the published snapshot; it does not trigger a robot capture.
- Blocker: the running HTTP camera sender supplies stereo colour but no depth/intrinsics; the map is acquired from the robot's existing mapping topic over a read-only connection. Continuous map streaming needs an integration-owned endpoint; no server restart or robot deployment performed.
- Removed the capture-trust subtitle, idle Ready label and Seer on/off control as requested. Desktop/mobile spatial checks pass, including rejecting a simulated manifest and recovering from an unavailable map. Chrome screenshots inspected.

### Telemetry panel styling — 2026-09-19
- Extended the entrance palette across robot views and telemetry cards with glowing panel edges, orbit rings, faceted polygons, stars and colored section markers. Decorations stay outside camera imagery, ignore pointer input and are hidden from accessibility navigation.
- Ring motion pauses offscreen and when the document is hidden; reduced-motion uses static shapes. Desktop/mobile screenshots inspected, stage and spatial checks pass. No new blockers.

### Full-page telemetry atmosphere — 2026-09-19
- Extended colored glow, orbit rings, diamonds, stars and particles across the fixed page background, visible through the margins and between panels while scrolling. Made the character stage transparent after its entrance so the background is visible behind it.
- Decorative layer ignores pointer input and accessibility navigation; motion pauses in hidden tabs and is static under reduced motion. Desktop/mobile screenshots inspected; stage and animation checks pass. No new blockers.

## h14 · robot · GET /map/voxels + a real pose (pose_bb from SLAM) + capture ids that cannot collide
Files:      robot/bbos.py (slam + map on the one hub thread; per-topic fault isolation), robot/server.py, robot/capture.py,
            robot/config.py, tests/test_robot_bbos.py (+4), tests/test_robot_server.py (+4), docs/16 §2.1c + §2.2, RUNBOOK §5
Verified:   read-only on the robot, this code in memory: slam() ok, pgo_count 249; map 56,354 voxels; SLAM vs the map's own
            robot_pos/heading: 3.7 cm, 0.0094 rad. Quaternion order settled by data: scalar-last gives 0.381 vs the
            map's 0.396; scalar-first gives -2.93. slam.pose age in steady state: median 45 ms, max 395 (IMU control:
            2 ms). tests/ green. NOT pushed.
Blocked on: POST /drive -> bbos nav is NOT built: it is the first thing that would MOVE the robot, and that needs the
            user's own word and a person beside it.
Surprise:   Compressing the map cost 312 ms on the Jetson to save 600 KB — more CPU than reading it, on the computer that
            balances the robot; it now ships uncompressed. And adding ONE new topic to the hub killed the camera and the
            IMU in tests: a KeyError in the slam reader took down the single thread every topic shares. On the robot
            that is "a bbos update renames a field and the capture gate silently loses its tilt evidence". Each topic
            is now guarded separately. Third: /pose's x/z/yaw are the OLD quickstart's axes; the SLAM pose went into a
            new, explicitly-framed field instead of being poured into them.

### Telemetry heading spacing and color — 2026-09-19
- Pulled the first robot panel upward by 40 px on desktop and 70 px on mobile. Changed the telemetry wordmark to a lavender-to-purple gradient with a soft violet glow.
- Desktop/mobile screenshots inspected; entrance, skip and reduced-motion checks pass without overflow. No blockers.

## h14 · perception/pointcloud · bb_source takes any voxel source: bbos's 3 cm floor-labelled map, identity registration, honest yaw
Files:      perception/bb_source.py: any source with .res + .points(), floor from .floor_mask() or .floor (bbos's
            own label, dropped outright), map_gen from the source or its nav. 3 cm cells (min cells and the height
            rule scale with res). Lone speckle cells dropped before clustering. MapSnapshot (reads
            scripts/bbos_map.py's map.npz). identity_registration() (refuses a map whose floor isn't at z ~ 0).
            scan_into_bb refuses a map_gen mismatch. Fit.yaw_known(): a round footprint, or one under 3 cells across,
            holds its committed yaw through associate's YAW_BAND door. perception/tests/test_bb_source.py (+6).
Verified:   perception/tests 321 passed. 3 cm labelled map: every object found at every lattice angle, centres
            ≤ 2 cm, sides ≤ 1 cell (1.5 cells under 6 cm), round objects measured as their room-axis box as the
            schema says. G2 over 5 noisy passes on a 3 cm source: clean. Master's case pinned: a 13×3×18 and a
            10×10 cm object on 3 cm, raw yaw swinging >10° pass to pass, 7 unchanged passes and 12 real moves
            with the committed yaw held. With the hold disabled the same test fails (move 11: "turned" 30→40).
            Real map through c6's scripts/bbos_map.MapSource, unchanged: map_gen 2111 agreed, 51,174 → 29,354
            cells after bbos's floor label, 0.53 s per pass.
Blocked on: nothing. The desk zone is master's, measured from the map's table top.
Surprise:   1) settle writes a yaw only when the object MOVED, so a false turn only ever shows on a real move.
            A test that just rescans can't catch it; it has to move the thing. 2) A speckle cell floating over an
            object raises that column's "top", and the height rule then cuts the column loose as a second object.
            3) At 3 cm a mug is "round" (its sides differ by 1 cell), so it's measured as its bounding box: 15×15
            for a 12×9 mug turned 40°. That's the schema, not a bug.

## h25 · web · the watch loop is wired in: edge inlet, chores, pull requests, "verified by rescan" with proof
Files:      web/events.py (`room_state`, `chore`, `pr`, `nav` allowed; `nav` volatile like `telemetry`).
            web/roommate_api.py: POST /api/edge/event (what roomctl/watch_cli's publisher already posts to; it did not
            exist), /api/chores -> roomctl.chores, /api/prs + POST + /approve + /close -> roomctl.pr, /api/nav/snapshot
            from the last pushed `nav` (`stale` after 10 s). /api/room/ci gains `last_verified_job` and `watch` (the
            loop's last room_state, kept in ~/.cache/gitspace/room-state.json so it outlives a restart) and
            `misplaced` (a deleted row + an untracked row for one object = one thing in the wrong zone).
            Every write: a loopback peer with no forwarding header, or the cloud bearer (>= 32 chars, constant-time).
            web/landing/ledger.js (+ dash.css, index.html): out of place / chores / pull requests under the CI badge;
            nothing is drawn when there is nothing to say. web/pages/room-connections.js: "verified by rescan" goes
            green only on `last_verified_job`; amber with the loop's own reason when it is running and has proven
            nothing; the nav node goes amber on an old pose. dash.js counts drifted THINGS, not git rows.
            web/tests/test_roommate_api.py (+7), conftest (ROOM_STATE_FILE), API-FOR-PAGES.md, PAGES.md.
Verified:   web/tests 146 passed. End to end on a second server (127.0.0.1:8077) over a scratch clone of the room:
            roomctl.watch_cli.web_publisher() itself posted room_state + chore and both came out of /api/events;
            POST /api/prs -> approve moved `main` (merge d1c974f, "Approved-by") and left the working tree alone;
            the same approve with an x-forwarded-for header -> 401; last_verified_job survived a restart. In Chrome:
            "I meant that" -> PR #3 -> approve, and the connections graph's rescan node green with the job id.
            The real room.git was never written: no pr/ refs, clean tree, checked afterwards.
Blocked on: a restart of :8000 (the deployment lead's) — until then the live server has none of these routes and the
            ledger stays hidden. landing/ledger.js and landing/brand-gitirl.svg are new files for the next deploy.
Surprise:   1) the watch loop was already publishing to /api/edge/event; nothing was listening, and urlopen's error
            is swallowed by the loop's _safe(), so it failed silently. 2) approve returns no job_id and should not:
            the merge changes what main SAYS, the loop sees the drift on its next fresh pass and makes the job that
            then gets verified. 3) pr.propose() picks a free staging spot, not the pose the object was seen at, so
            an accepted "I meant that" still reads `modified` (39 cm in the test) and the robot would nudge it.

## h14 · robot · the robot was starved (load 41, 139 MB free): cut this server's own share of it
Files:      robot/bbos.py (per-topic poll rates, map Reader released after each read, self-measured `busy`),
            robot/server.py (/healthz.bbos), robot/allow.py (the allowlist, now shared with the adapter),
            robot/adapter.py (refuses to listen off localhost without token AND ROBOT_ALLOW), tests (+3)
Verified:   124 robot tests, 4 runs, stable. NOT measured on the robot: sshd there was timing out under the load.
Blocked on: the link session's next push; then `curl :8080/healthz` -> bbos.busy says what the hub really costs.
Surprise:   Asked "what is robot.server spending 16.5% CPU on with no clients?", the honest answer was in my own code:
            five bbos topics polled at 200 Hz — ~1000 ready() calls a second, each two syscalls and a copy — under a
            tap that samples at 50 Hz and a SLAM that publishes at 28. Now ~290/s. And bbos's Reader keeps two full
            copies of a slot for as long as it lives: reading the 36 MB map once pinned 72 MB for good, on a robot
            with 139 MB free. It is now opened, read and dropped. Neither showed up on a laptop; both are the kind of
            cost that only matters on the computer that is also keeping the robot upright.

## h26 · web · the live map and the blame card: the dashboard now answers "what is the robot doing" and "who moved it"
Files:      web/landing/livemap.js (+ dash.css, index.html): the nav grid painted once per map as a bitmap, freshness
            tint, dashed patrol path, the robot with a nose; follows SSE `nav`, refetches the snapshot on a new map_gen,
            absent from the page until something publishes. web/roommate_api.py: a pose-only nav event keeps its map,
            a new map_gen without a grid drops the old one; /api/blame gains `moved_in.proposed_by` (a PR's commit is
            authored by the robot's identity; the person is its trailer) and a real `frame_url` — only a frame that
            camera_ingest filed under THAT capture id. web/landing/dash.js: `who?` on each drifted object (once per
            object) -> the blame card; it survives the list's redraws. API-FOR-PAGES.md now holds the one definition of
            what a nav publisher sends, since no publisher exists yet and 03 §8 leaves cells_b64 and yaw units open.
Verified:   web/tests 147 passed. In a browser at 390 px and 1280 px on a second server over a scratch clone: hidden
            before any nav; a pushed 60 x 40 map drew without seams; seven pose-only events moved the robot along its
            path; a grid of the wrong length was NOT drawn and the caption said "60 x 40 cells and 17 arrived"; 11 s of
            silence turned the robot hollow with "this is where it WAS". who? -> "last moved 39 cm, zones/shelf ->
            zones/desk — a pull request by you". No horizontal overflow, no page errors.
            The map and poses in that check were made up and pushed to the scratch server only; the frame in the
            picture check was a real stored frame's URL swapped into the response in the browser — nothing was
            written under landing/live/, and no story capture has a frame, so live blame answers say so.
Blocked on: a nav publisher (nobody's code emits `nav` yet) and the same restart of :8000 as h25.
Surprise:   the robot pose's yaw is radians in docs/20 while every object record's yaw is degrees; the snapshot line
            in 03 §8 says neither. The page reads radians and accepts `yaw_deg`.

### Telemetry navigation cleanup — 2026-09-19
- Removed the visible intro skip button and telemetry's Overview navigation link. The entrance still finishes automatically; Escape and reduced-motion remain supported.
- Desktop, mobile, Escape and reduced-motion stage checks pass; animation checks pass without page errors.

## h00 · elastic · health check of the live indices: three upstream data gaps
Verified:   18:30 UTC, read-only. Live key still 4J1Ot6ABHRDtKTe8PfFD (rotation waits on Kibana).
            Index state: objects 67 (all vlm_model fake/scene_gen, all 6 commits in room.git),
            voxels 6,657 · clouds 19 · observations 3,101 · events 43 · telemetry 1.96M.
            "Where are my keys" still top-1 on main with a 0.31-0.37 margin.
            GAPS (none Elastic's, all hit Elastic-facing beats; owners told):
            1. telemetry STOPPED: every signal's newest sample is 17:59:48Z (31 min before the check).
            2. odom_residual stopped 11 h earlier (07:07:11Z; 6,854 docs vs ~280k for the others) —
               the "why was this diff wrong" residual spike is unavailable after that.
            3. cap_0016/0018/0020/0021 (17:18-17:25) wrote a room-clouds doc each and ZERO
               room-observations (every capture to cap_0013 has 36-42; no stray index). So
               hidden-vs-gone, occlusion history, "cameras disagree" and moved_at's frame views are
               stale after 06:30. No camera "bb_map" docs yet.
            Also: 38 `robot_failure` events (branch unset) — a third event vocabulary next to docs/11's
            and §10's. Strict mapping accepts it; flagged so the pitch and dashboards agree.
            Note for serverless: `GET <index>/_stats` is 410 (not available); use counts/ES|QL instead.
Blocked on: key rotation (Kibana); upstream fixes for the three gaps above.
- Navbar consistency: telemetry now inherits the main page's shared sitenav background, blur and sticky behavior; removed page-specific overrides. Overview remains omitted as requested.
- Observed integration blocker during localhost verification: robot link reports unreachable (10.37.101.235:8080); live camera displays its offline state. Saved hardware snapshots remain available. No connection or service changes made.

### Floor chip-packet segmentation — 2026-09-19
- Fixed the existing floor-path integration in perception/pipeline.py and segment.py: automatic for floor zones, independent of the image model, detector range measured in the robot frame before room-pose transformation. Kept table-only processing unchanged.
- Prevented rejected floor pixels from entering the desk clusterer: cap_0007 previously produced 85 phantom residual clusters; cap_0015 produced the packet plus 45 false residual clusters. Floor zones now use the noise-aware floor detector exclusively for geometric segmentation.
- Real cap_0015 packet: one mask, 941 measured points near (0.44, -0.90) m. End-to-end test writes exactly one floor object. Four empty-floor captures return none; moved-pose regression preserves the same mask. Overlay inspected at /tmp/floor-packet-segmented.jpg.
- Enabled a floor zone in the local ~/.cache/gitspace/rooms/hallway-map/room.yaml and removed its explicit zones/floor/** exclusion. No room commit, project commit or push; no server restart.
- Limits/blockers: robot currently unreachable through localhost camera status, so live recapture cannot be verified. Very flat wrappers remain below measured depth noise; labels remain unknown without semantic recognition. Shared temporary-directory interference affected one broad test run; rerunning with a dedicated temporary root.
- Final validation: perception suite passed with dedicated temporary root — 334 passed, 12 skipped, 1 expected failure. The floor-specific suite includes real packet, empty-floor, moved-pose and end-to-end serialization cases.

## h18 · perception/segment · D14: real discarded clusters now index as the capture page's discard pile
Files:      perception/cluster.py (rejects=, size_reject_reason), perception/segment.py (lift/run rejects=),
            perception/merge.py (observation_row), perception/pipeline.py (ScanResult.rejected, capture_docs),
            perception/tests/{test_cluster,test_segment,test_merge,test_pipeline}.py, docs/15, docs/10 D14
Verified:   `.venv/bin/python -m pytest -q perception/tests` → 337 passed, 12 skipped, 1 xfailed;
            G2 `tests/test_idempotent_scan.py` 16 passed, 8 skipped;
            `python3 scripts/audit_architecture.py` → 18 ok · 1 warn · 0 FAIL (warn is robot/telemetry/obs vs
            docs/16,23,18 — not this track).
Blocked on: nothing for the index path. /capture will show the pile once a real capture is indexed
            (`GITSPACE_INDEX_CAPTURES=1`); not pushed, not committed.
Surprise:   `capture_docs` stamped every observation in a capture with the same `@timestamp`. That was
            fine while each row had its own `object_id`. The discard pile all share `object_id: null`,
            so one timestamp would have made TSDS collapse the whole pile to a single document
            (docs/13). `associate.observation_docs` already incremented milliseconds per row;
            `capture_docs` did not. Also: an out-of-zone `keep()` drop is *not* a discard-pile reason —
            only size / no-depth / .roomignore. Handing those pixels back to the fallback is the
            dining-table rule, not a reject.

## h19 · perception/segment · hallway floor objects commit without a floor zone in room.yaml
Files:      perception/pipeline.py (ensure_floor_zone, FLOOR_CLOUD_FRAC, strip zones/floor/** on
            a floor scan), perception/segment.py (FLOOR_Z_MAX: desk-height "large" is not withheld
            from cluster), perception/tests/test_floor_pipeline.py, test_segment.py, test_pipeline.py,
            docs/15, docs/10
Verified:   `.venv/bin/python -m pytest -q perception/tests tests/test_idempotent_scan.py` →
            355 passed, 20 skipped, 1 xfailed. Native `datasets/now/cap_0015` (desk-only room.yaml,
            `zones/floor/**` in .roomignore) writes one `zones/floor/` file at (0.44, -0.90). Empty
            hallway captures stay empty. GITSPACE_FLOOR=1 on the synthetic desk still commits 3
            table objects. Audit 18 ok · 1 warn · 0 FAIL (warn is robot/telemetry/obs, not this).
Blocked on: a nearly flat wrapper is still below the detector's height/noise floor; labels stay
            `unknown` without a VLM. Not committed.
Surprise:   The detector already found the packet. scan_into dropped it because every recording
            ships the desk template: no floor zone (keep() / for_serialize have nowhere to put
            a centroid at z = 6 cm) and `.roomignore` says `zones/floor/**`. Tests had been
            rewriting both. Clipping the finder to z < 0.40 m looked right and was wrong: a
            person becomes a pile of feet, and cap_0015 jumped from 1 object to 6. The desk
            steal was only the "large" residual mask; leave the finder the whole frame.

## h20 · perception/segment · colourful chip bags on the floor seed by saturation, not height
Files:      scripts/floor_objects.py (SAT_MIN / SAT_MARGIN / K_PACK / PACK_H_MAX / MIN_WIDTH_M,
            pack seed ORed into seed/cand, free_standing skipped under 8 cm, wide_enough),
            perception/tests/test_floor_pipeline.py (two bags + chair-sliver regression),
            docs/15, docs/10, scripts/README.md
Verified:   `.venv/bin/python -m pytest -q perception/tests tests/test_idempotent_scan.py` →
            356 passed, 20 skipped, 1 xfailed. Native `datasets/now/cap_0015` writes two
            `zones/floor/` files: packet at (0.46, -0.90), wrapper at (1.33, 0.14). Empty
            hallway captures stay empty; cap_0013's can still one object; cap_0014 chair-foot
            slivers are gone. Overlay `/tmp/floor-cap0015-packs.jpg`. Audit 18 ok · 1 warn ·
            0 FAIL (warn is robot/telemetry/obs, not this).
Blocked on: labels stay `unknown` without a VLM. A floor-coloured book, phone or cable still
            never seeds. Not committed.
Surprise:   The wrapper was not "too flat for stereo" in the interesting sense — 16×13 px,
            height 4 cm, which is ~1σ at 1.3 m, so K_SEED × σ (11 cm) can never see it. Grey
            lino sat p90 is 18 and the bags are 100+. Relative sat vs this capture's blank
            floor is what keeps an orange floor from seeding itself. Pack seed without
            MIN_WIDTH_M invented 1–2 cm chair-foot slivers on cap_0014. A 4 cm bag has no
            matcher-visible shadow, so free_standing would have killed the wrapper.

## h21 · perception/segment · chip-bag masks are the bag, not the grey stereo halo
Files:      scripts/floor_objects.py (PACK_SEED_FRAC / PACK_CORE_FRAC: grow pack-seeded
            masks from the saturated core while they stay majority-colour),
            perception/tests/test_floor_pipeline.py (median sat >= 40 on both bags),
            docs/15, docs/10
Verified:   `.venv/bin/python -m pytest -q perception/tests tests/test_idempotent_scan.py` →
            357 passed, 20 skipped, 1 xfailed. cap_0015: packet 625 px at (0.46, -0.90),
            wrapper 100 px at (1.30, 0.17), sat medians 85 / 61. Can, empty floors, chair
            unchanged. Overlay `/tmp/floor-cap0015-packs.jpg`. Audit 18 ok · 1 warn · 0 FAIL
            (warn is robot/telemetry/obs, not this).
Blocked on: labels stay `unknown` without a VLM. Not committed.
Surprise:   Finding the bags was the easy part. The component is 60–80 % grey halo — SGBM
            smears the bag's disparity onto blank lino, and that lino is "textured" out to
            BLOCK_R because of the bag's own edge. Peeling low-sat from the outside ate the
            white print (white is sat 0, same as the floor). Growing the colourful core
            while pack-pixel fraction stays ≥ 0.55 keeps the bag and drops the glow: k=5
            on the near packet, k=3 on the far wrapper. A can is 14 % pack and is not grown.

## h00 · elastic · MVP: resolve_object for the resolver + a smoke check for every Elastic beat
Files:      elastic/queries.py (resolve_object), scripts/check_elastic_beats.py (new, read-only),
            ANDREW-HANDOFF.md §2b, docs/11-elastic.md (event vocabulary), elastic/mappings/room-clouds.json
Verified:   live suite 154 passed. `scripts/check_elastic_beats.py` → 7/7:
            find 3/3 top-1 keys_7c2e (margins 0.372/0.309/0.324) · resolve "the thing I cut paper with"
            → scissors_9f3a (margin 0.147) · showpiece cup_7e21 #2, BM25 misses it, vector 0.665 ·
            blame mug_a1b2 → 1a668ec0 (cap_0005), 3 camera views · time travel now → 1a668ec0,
            strictly-before → b3691ead · why cap_0021 7 signals peak 5.100 · hidden-or-gone 218
            captures in 7d. The find beat needs margin >= 0.10, not just the right rank.
            room-clouds gained `objects` (integer), file + live index, before perception emits it
            (strict mapping: emitting first would reject the whole cloud doc).
Blocked on: key rotation (Kibana step); the robot being back for live telemetry.
Surprise:   CORRECTION to my previous entry: cap_0016-0021 were not a broken writer. The scans
            honestly found zero objects (demo-bench zones over LINK's hallway). What was broken is
            that a cloud doc had no way to say "looked, found nothing", so honest silence and a
            broken writer looked identical — which is what the investigation cost. `objects` now
            distinguishes absent (never scanned) / 0 (scanned, empty) / N.

## h15 · perception/pointcloud · bb_map observations: a real map pass writes what /capture/<id> reads
Files:      perception/bb_source.py (live_source, map_scan_docs, index_map_scan, scan_into_bb(es=)),
            perception/tests/test_bb_source.py (+4).
Verified:   perception/tests 346 passed, 12 skipped. A real pass writes one room-observations row per
            object, camera "bb_map" (perception-02's observation_docs(camera=)), plus the room-clouds
            catalog doc and its .ply, all through es_sink with the pass's Sentry trace. Read-only
            against the LIVE index: `camera` is a keyword time_series_dimension and a bb_map row has no
            field the live mapping lacks, so nothing has to change there before this writes.
            No gate fields on the cloud doc: a map pass has no shutter, so skew_ms / tilt_rate_max /
            coverage would be invented.
            Guards, both tested: the GITSPACE_INDEX_CAPTURES default also needs publish.is_the_room
            (roomctl/cli.py:99's rule, so a scratch or sim repo writes nothing), and live_source() is an
            ALLOW-list over the source — a MapSnapshot or scripts/bbos_map pull, or a nav on a
            non-loopback host. bbsim binds loopback by construction, and anything whose provenance we
            cannot name is refused with a reason in the log.
Blocked on: nothing. A map pass's cloud doc also carries perception-02's `objects` count (absent =
            never scanned, 0 = scanned and found nothing, N = seen), so both paths answer that question
            the same way on /capture/<id>.
Surprise:   1) The captures after 06:30 were not a broken writer: I replayed cap_0004 with a fake ES and
            got ok=True, objects=0, docs={'room-clouds': 1}. Those recordings carry the demo bench
            room.yaml (desk 0.08-1.00 m), and LINK's hallway has nothing inside those boxes, so zero
            rows is the honest answer. It reads like a bug only because a cloud doc cannot say "I looked
            and found nothing".
            2) My first guard asked "is this a real map?" before "is this a sim host?", so a saved real
            map replayed through a loopback harness qualified as real. Any sim signal now wins.

## h19 · perception/segment · the can survives the mask path; a cloud doc can now say "I looked and found nothing"
Files:      perception/segment.py (floor_masks + _floor_finder, run(floor=), lift(min_points=, source=, erode=),
            FLOOR_MIN_POINTS / FLOOR_DUP), perception/pipeline.py (capture_docs: assocs=None, `objects`),
            perception/tests/test_segment.py (+4), perception/tests/test_pipeline.py (+1),
            fake/README.md (room-clouds contract: the three states), docs/15 (the lift, and the halo limit)
Floor:      scripts/floor_objects.find_floor_objects is loaded by path under ONE module name (the two-copies
            bug, docs/10 02:27) and its masks go through the same mask -> 3-D lift, with two differences:
            FLOOR_MIN_POINTS 40, not 150 (a 5.3 x 13.5 cm can 1.3 m out is 222 px, and 150 is sized for a
            desk), and NO erosion (4 px off the edge is 173 of those 222 pixels; these masks are cut by
            physics, not by a model's soft boundary). A floor object >= 50% inside a mask the image model
            already claimed is dropped, and what the finder calls "large" is withheld from the residual
            like an ignored label.
            Real cap_0013, no model involved: one instance, 222 points, centre (1.27, -0.43) in 0.26 s.
            Scanned into a repo with a floor zone it commits at (1.23, -0.44) against the robot link's
            hand measurement of (1.24, -0.43), and a second pass is 13 unchanged with an EMPTY diff.
Limit:      SGBM's halo fattens small things: that can's box reads ~12 x 7 x 7 cm (raw mask: 19 x 11 x 10)
            for a 5.3 x 13.5 cm can. Trimming along the ray does NOT fix it -- at +-6 cm it reads
            13.8 x 11.6, at +-4 cm the height collapses to 6.9 from a true 13.5 -- so the points are left
            alone and the limit is pinned in a test. It matters for associate's extents-ratio gate, which
            compares the same object seen at two ranges; the map path's Fit is loose for its own reason
            (3 cm cells), so both callers of that gate now have soft sizes.
Observations: the four captures with clouds and no observations were NOT a broken writer (elastic's report,
            corrected by them since): those recordings carry the demo bench's zones and the hallway holds
            nothing inside them. Zero rows was honest, but a cloud doc could not SAY so, which is what cost
            the investigation. capture_docs now carries `objects`: absent = catalogued, never scanned;
            0 = a scan looked and found nothing; N = N objects, the capture's row count. `assocs=()` could
            not be told from "no scan", so its default is None now.
            Before, live and read-only: cap_0013 objects ABSENT / 42 rows; cap_0016, 0018, 0020, 0021
            objects ABSENT / 0 rows.
            After, offline against a fake ES: synthetic desk -> objects=3, 6 rows · real hallway cap_0016
            -> objects=2, 7 rows (floor path as shipped) · the same with GITSPACE_FLOOR=off -> objects=0,
            5 rows (the discard pile still says "I looked") · catalogued only -> objects absent, 0 rows.
Verified:   perception 346 passed, 12 skipped, 1 xfailed. elastic 153 passed. The live room-clouds mapping
            read back read-only: objects {'type': 'integer'}, dynamic strict — elastic-09 had already put
            it on the file AND the live index, so nothing emits into a strict mapping that lacks it.
Blocked on: the bb_map half is perception-f5's and was written in parallel.
Surprise:   1) A floor zone commits what the fallback clusterer finds on that floor: on cap_0013 the can
            arrived with 12 phantom neighbours, which is what the shipped .roomignore `zones/floor/**` was
            keeping out. Whoever owns h18/h19 in this track should see that before it runs on the real room.
            2) The laptop's disk hit 100% (117 MB free) mid-run, which is how the ENOSPC surfaced: a
            scratch copy of the repo I had left behind was part of it.

## h00 · elastic · room-clouds `objects` verified live; the cap_0016-0021 correction closed
Verified:   read-only, 21:20Z. Live mapping: room-clouds.objects {'type': 'integer'}, dynamic strict.
            Every capture reads `objects` ABSENT today — nothing has scanned since the field landed:
            cap_0013 absent / 42 observation rows, cap_0016·0018·0020·0021 absent / 0 rows. Absent is
            the truth for them: capture_to_recording catalogued them and no scan ever ran.
            The three states are in fake/README.md's room-clouds line (perception's edit, line 102):
            absent = catalogued, never scanned · 0 = a scan looked and found nothing · N = N objects,
            which is that capture's observation count before the discard pile's object_id null rows.
            Beats check after master's commit 3ed04fa: 7/7, same margins (keys 0.372/0.309/0.324).
Blocked on: key rotation (Kibana step).
Surprise:   Nothing new — this closes the earlier wrong call. Worth keeping: "a scan ran and found
            nothing" and "nothing ever scanned" look identical unless a field says which, and the
            discard pile (object_id null rows with a rejected_reason) is what makes an empty scan
            legible rather than silent.

## h00 · web/landing · the dashboard's only long task was mine; and the :8000 outage was eight forked children holding the socket
Files:      web/landing/roommate.js (one shared shader program, mount deferred to idle, watches the stage's PARENT),
            robot.js (paintRobot bakes uPosScale in as a literal so the robot compiles ONE program, not twelve),
            tools/dev/dashperf.mjs (new: FCP/LCP/TBT/CLS/bytes/heap plus a 6 s idle watch, per page),
            tools/dev/roommate.mjs (a `native` mode: the real page loading roommate.js by its own tag, and the hero
            page proving it stays clean). No file of another session edited.
Verified:   THE OUTAGE. :8000 answered about 1 request in 9. Nine processes held its LISTEN socket: the server (24354)
            and eight forked children, all 0.0% CPU, each deadlocked inside fork before exec. A read-only `sample` of
            83203 ended subprocess_fork_exec -> fork -> libSystem_atfork_child -> _malloc_fork_child ->
            msl_turn_off_stack_logging -> msl_printf -> write. macOS spreads accepts across every holder of the socket,
            so most connections went to a dead child. Reported with the pid list; master owns :8000 and restarted it.
            MY OWN COST. /?info has no long task of its own: every millisecond of its Total Blocking Time was this
            module. styles2 gives each mesh its own shader program on purpose (its uPosScale comes from each mesh's
            bounds), but paintRobot already gives every mesh the SAME robot-wide value, so twelve identical compiles
            were being paid. Baking the value in as a literal makes the shaders byte-identical: 14 programs -> 3,
            TBT 144 ms -> 50 ms, worst task 194 -> 100. Deferring the mount to requestIdleCallback took it to
            TBT 32 ms / 82 ms, and the frame is pixel-identical. The hero page still mounts nothing at all.
            THE PAGES, after master's restart carried web-64's git fix (`node tools/dev/dashperf.mjs <out> <base>`):
            /?info FCP 552->88 ms, LCP 740->172, TBT 152->38, CLS 0.217->0.093. /robot TBT 287->22. /telemetry
            TBT 37->18. All three at 60 fps. In steady state /telemetry drops ONE frame (37 ms) in 15 s.
Blocked on: nothing.
Surprise:   Two things I reported were wrong and the measurement caught both. I called /robot's heap a leak from
            +5.86 MB over 6 s; sampled properly after a 15 s settle it is -10.5 MB per minute, i.e. it was still
            loading, not leaking. And I proposed thinning its 388k-point capture: intercepting the .ply and keeping
            35% moved LCP 828 -> 776 ms and heap 187 -> 176 MB, because the largest paint is a SPAN of text, not the
            cloud. So the cloud stays at full fidelity. Also: I advised close_fds=True to avoid the fork and had it
            exactly backwards — CPython takes posix_spawn only when close_fds is FALSE; web-64 measured it and fixed it.

## h12 · cloud · beat 5 end to end on the simulated robot: a sentence to a point, through Andrew's edge
Files:      scripts/room_clean_beat.py (new), web/housebot.py (records `simulated`), bridge/caretaker.py
            (the Elastic resolver with a margin tie-break; restore_time plans; why reads), bridge/intents.py
            (INTENT_URL hook; `why` rules), bridge/intent.schema.json (`why`, optional `ref`),
            bridge/agent_api.py (a moment that has no commit says so as a MOMENT), .env (HOUSEBOT_EDGE_URL /
            TOKEN, point+move allow-listed — localhost only), docs/10 D49/D50, tests +9
Verified:   bridge 69 · web 153 · telemetry 57 green. Beat 5, localhost, with the panel's own endpoint:
              panel "point at the mug" -> route caretaker, served_by gitspace:grammar (no INTENT_URL set)
              intent point object_query='mug' source=grammar confidence=1.0
              resolve mug_a1b2 (mug) in desk via elasticsearch score=1.385 margin=0.302
              job job_c0f085405b8fcf79 target_pose {x 0.61, y 0.18, z 0.75, yaw 40} frame world_z_up
              dispatch -> http://127.0.0.1:8780 (Andrew's edge, his 7a31596) -> robot/adapter.py --sim
              adapter success: "SIMULATED: pointing at mug_a1b2 from 0.60 m, room heading -82°"
              job succeeded, simulated=true recorded by us, 1.3 s end to end
            Also live: "the way it was 2 hours ago" plans (ref_resolved 1a668ec); "before dinner" answers
            "no commit on main before 2026-09-18T18:00-04:00"; "why was this diff wrong" returns the join.
Blocked on: the Sentry `room-clean` monitor is created but DISABLED — the plan's one cron seat is held by
            the muted, erroring `watch-loop`, and enabling answers "not enough pay-as-you-go to create a new
            seat". The user decides: free the seat or leave the badge recorded-only.
Surprise:   The last hop refused on a NAME, not on geometry: robot/frames.py carried `canonical_world_z_up`
            (Andrew's old constant) while the wire, his current code and 53 places in the repo say
            `world_z_up`. And `odom_residual` never came from the real robot at all — until 07:07Z every
            signal arrived in equal counts from the SIM, and the real robot's source simply doesn't compute it.

## h27 · web · the server stops forking, "before dinner" becomes a commit, and beat 4 answers in the panel
Files:      web/room.py: git is run by posix_spawn, never fork — absolute git, `-C` instead of cwd, close_fds=False.
            web/scene_api.py (another session's file; master asked me to): the same, at all four spawn sites.
            web/tests/test_no_fork.py (new). web/graph_api.py: resolve_state also resolves a TIME phrase
            (how "time"), GET /api/when?phrase= and GET /api/why/{ref}. web/es_shared.py: client(), the official
            elasticsearch client the roomctl joins need (web's own Elastic is an async httpx proxy).
            web/pages/room-chat.js: renders `as: "why"` and names the commit a time phrase landed on.
            web/landing/dash.js: the CI badge printed "since [object HTMLTimeElement]" — a <time> ELEMENT in a
            template string. web/landing/livemap.js: the freshness wash was drowning the map.
            web/tests/test_when_and_why.py (new). 176 tests pass.
Verified:   NO FORK: CPython 3.11.9 takes posix_spawn only when close_fds is FALSE (read out of the installed
            Popen._execute_child, not from memory); close_fds=True is what forced the fork. Safe because PEP 446
            already makes every Python-created descriptor non-inheritable — checked with the server's own listening
            socket and an os.dup of it, both `closed` in the child. Under MallocNanoZone=0: 1,440 + 240 requests on
            the git-backed endpoints, then 320 + 80 on the two scene endpoints /robot polls; a 250 ms sampler on the
            listening socket saw ONE pid throughout and zero children, both times.
            BEAT 4, in Chrome at 430 px on :8000, all three sentences: "where are my keys" -> keys_7c2e, job sent to
            the housebot edge; "put the room back the way it was 2 hours ago" -> "2 hours ago is commit 1a668ec",
            0 ops, nothing moved; "why was this diff wrong" -> capture cap_0005, gate 2.1 ms / 0.005 rad/s, coverage
            76%, peak tilt 0.012, odometry 0.4 cm, capture + replay links, the Sentry trace. No page errors.
Blocked on: THE DISK. 6.5 GB free of 460 (99% full). The sim watch loop has already died once with
            "OSError: [Errno 28] No space left on device" writing .git/gitspace/misses.tmp, which is why the sim
            room sat on a confirmed mess (tidy-3) for 44 passes with 8 stale blocks. Beats 1 to 3 cannot be
            trusted until there is room. ~11 GB sits in two stale scratch directories under /private/tmp/claude-501
            (9.1 GB and 1.8 GB, nothing touched in either for two days) — the user's call, not mine.
Click path: BEAT 1 (sim, :8001/?info): the badge reads `room-clean passing`, "where the roommate is" shows the
            3 cm grid with the robot on its patrol path, no chores, no pull requests.
            BEAT 2: `python scripts/demo_sim.py mess mug_a1b2`. The badge flips to `failing`, the change list shows
            `modified: mug_a1b2 · zones/desk · moved N cm`, and the loop tidies it; when a clean FRESH pass follows
            the job, the badge returns to `passing` and /robot > Connections turns "verified by rescan" green with
            the job id in its reason.
            BEAT 3: `demo_sim.py mess lamp_2d9b`, then on the dashboard press `I meant that` on its OUT OF PLACE
            row (opens an as_seen pull request), then `approve` on that row under PULL REQUESTS. Clicked, not
            scripted: the badge went from failing to "nothing to commit, working tree clean", the lamp stopped
            being drift, and PR #2 reads "lamp_2d9b lives here now · moved 5.0 cm · merged e57a014".
            From a phone the first write answers 401 and the page asks for the room's token once.
            BEAT 4 (real, :8000/robot, the Agent panel): "where are my keys" · "put the room back the way it was
            2 hours ago" · "why was this diff wrong".
Beats run:  1, 2 and 3 all pass on :8001 in Chrome at 430 px, after master cleared the disk.
            MEASURED, one run each, by a sampler polling /api/room/ci every 2 s (so every figure is +/- 2 s, and
            these are single runs, not averages — the runbook should quote them as such):
              beat 2, `mess mug_a1b2` at 17:45:29
                +2 s   the badge goes red        (dirty, 1 pending)
                +18 s  the mess is CONFIRMED     (mug_a1b2, job tidy-2 — two fresh passes, as the debounce wants)
                +24 s  the badge goes green      (clean, last_verified_job tidy-2)
                26 s total. "verified by rescan" in the connections panel then reads green: "tidy-2 was followed
                by a clean fresh pass".
              beat 3's own mess, `mess lamp_2d9b` at 17:48:18
                +2 s red · +10 s confirmed (tidy-3) · +54 s green and verified. 56 s total.
            So the robot puts a thing back in roughly half a minute, and the two beats differ by 2x — 26 s and
            56 s — because the debounce waits for whole scan passes, not a clock.
            ONE THING THE RUNBOOK MUST SAY: in beat 3 the loop is racing you. It confirmed the lamp 10 s after
            the mess and had tidied it back within a minute, so "I meant that" has to be clicked in that window.
            Clicked later (which is what happened here) the row is still there, but the drift being approved is
            the few centimetres the tidy left behind — I approved 5.0 cm, not the 18 cm that was moved. The beat
            works either way and the end state is right, but the honest instruction is: make the mess, then click
            within about twenty seconds, before the roommate wins.
            Three of my own gaps came out of running them, all fixed here: "I meant that" only appeared when an
            object changed ZONE (the lamp moved 5 cm INSIDE its zone, which git reports as one `modified` row, so
            beat 3 had no button at all); /api/edge/event refused the watch loop's `job` events with a 400 though
            03 §8 lists `job` alongside the other four (roomctl/caretaker.py has a comment about that 400); and
            the connections panel showed the camera RED on the sim for "no viewer yet", when nothing had asked it
            for a frame — red means failing, so it is grey until `polls` says it actually tried.
Surprise:   1) a sync `def` FastAPI endpoint runs in a worker thread, so /robot's 5-second poll of /api/scene/*
            was forking from a thread every 5 seconds for as long as one tab was open — that, not anything in the
            request path, is what kept making dead children. 2) /usr/bin/git is the xcrun shim: 80 ms a call
            against 41 ms for the real binary. /api/prs went from 0.4–1.9 s to 0.03–0.09 s and first contentful
            paint on /?info from 552 ms to 88 ms (measured by the performance session, independently).
            3) "before dinner" resolves to YESTERDAY 18:00 before dinner time, so on the real room it answers
            "no commit on main before then" — correct, and it will resolve during an evening demo.

## h00 · web/landing · a frame-budget gate for the beat pages, and the baselines it starts from
Files:      web/landing/tools/dev/framewatch.mjs (new). Nothing else touched.
Verified:   `node tools/dev/framewatch.mjs <base> <pages> [outdir]` prints PASS/FAIL per promise and exits non-zero on
            any failure, so it can gate a commit. It hooks getContext before any page script runs (a second WebGL
            context is caught even when its canvas is hidden, offscreen, or created and discarded) and instruments the
            GL context itself: draw calls per frame, and live textures/buffers/programs as created-minus-deleted.
            BASELINES. /telemetry on :8000 green: 1 context, 60 fps, worst frame 22 ms, 0 frames over 30 ms in 15 s,
            133 draw calls/frame, 5 textures / 496 buffers / 13 programs, no drift, clean console. :8001/?info green:
            1 context (the roommate stage), 60 fps, 3 programs, 0 draw calls/frame — its loop is correctly stopped
            while off screen. :8001/robot FAILS the one-renderer rule with TWO contexts (cloud-canvas, room-frame);
            it is still 60 fps with an 18 ms worst frame, so the cost is memory (~185 MB), not the frame budget.
Blocked on: nothing.
Surprise:   Both of the gate's own first answers were wrong in the same way, and for reasons worth writing down. It
            looked for a THREE.WebGLRenderer on `window` and printed dashes, because a page that keeps its renderer
            private inside a module is doing the right thing — going through the GL API instead works everywhere.
            And a fixed settle before measuring made a page that streams 10 MB of point clouds look like it was
            leaking; waiting for the heap to stop moving first turned that FAIL into a PASS. A gate that cries wolf
            on the heaviest page is worse than no gate, because that is the page people would have stopped believing.

## h00 · web/landing · the second renderer on /telemetry only appears when you click, so idling never found it
Files:      web/landing/tools/dev/framewatch.mjs (--exercise, and it now names the file that asked for each context).
Verified:   `node tools/dev/framewatch.mjs http://127.0.0.1:8000 /telemetry <out> --exercise`. Frame budget after the
            telemetry work landed is UNCHANGED: 60 fps, worst frame 27 ms, 0 frames over 30 ms in 15 s, ~133 draw
            calls/frame, and nothing drifts once hands are off. Three failures, none of them the frame budget:
            (1) TWO WebGL contexts — canvas#seer from mountSeer (pages/seer/seer.js:393) and an unnamed, never-sized
            300x150 canvas from createSpatialViewer (pages/seer/spatial-viewer.js:5), which builds its own renderer
            instead of sharing. (2) Clicking every control three times over grows live GL buffers 503 -> 507 -> 511,
            four per pass, never freed, while textures (9/9/9) and programs (14/14/14) stay flat. (3) GET
            /api/telemetry/sentry/cap_1003 returns 502. Reported to master with the addresses.
Blocked on: nothing.
Surprise:   The rule was "one shared renderer", and the page kept it perfectly while nobody touched it — the second
            context is created on the first click, so an idle check passed it three times. A gate that only watches a
            page sit there tests the one state a demo never stays in. Pressing every button three times over is what
            turned an invisible pass into an address and a line number. The same run also cost me two corrections to
            the gate itself, both the same error in different clothes: it read counts mid-upload (it waited on the JS
            heap, and GPU buffers do not live there), and with an exercise running it compared before-clicking to
            after-clicking, which measures the work the clicking did rather than anything leaking.

## h16 · perception/pointcloud · DEMO-RUNBOOK re-rehearsed against the MVP beats; the phantom-after-a-move transient found
Files:      docs/DEMO-RUNBOOK.md (rewritten for the five MVP beats).
Verified:   Every line in it was observed. Beat 1: `demo_sim check` six of six ok. Beat 2 from a reset room,
            sampling /api/room/ci every 1.5 s: pending 0.2 s, CONFIRMED tidy-1 6.5 s (bbsim -> navigate), the mug
            IN THE ARM'S HAND 9.6 s, clean and verified 12.7 s (web-64's clicked run: 26 s; the runbook says
            "about half a minute" and quotes both). Beat 3: PR #1 opened as seen and approved, clean at 33 s
            (web-64: 56 s), with master's twenty-second click race written into the step. Beat 4 on :8000: keys
            resolve (score 1.454, margin 0.45) and `room why` (cap_0005 gate PASSED, verdict trustworthy) both
            good; "put THE ROOM back ... 2 hours ago" resolves 1a668ec with 0 ops. Beat 5: the point job reaches
            Andrew's edge through POST /api/object-life/<id>/point (executor "housebot-edge", dispatched) but NOT
            from the panel sentence (executor "not_connected", twice, after two restarts).
Blocked on: nothing of mine. #1 (beat 2 stalling about one run in four) is gitspace-22's, #2 (the pronoun in the
            panel's restore grammar) and #4 (the panel never calling housebot.submit) are gitspace-d2's.
Surprise:   1) A move leaves PHANTOM objects: bbsim's map keeps an object's cells at its old pose until the robot
            looks there again, so there is one blob more than there are records. Beat 3 sampled 2-4 phantoms at a
            time for ~30 s around the lamp's old pose, each pass minting a fresh unknown_* id, each pending as
            lost_and_found. Usually they clear in a second or two; web-64 caught one surviving two fresh passes
            and being CONFIRMED under a neighbour's name (glasses_case_d04f:tidy-1 from a `mess mug_a1b2`, 16 cm
            away). So a chore or a tidy can be minted for something nobody touched. I tested four other
            explanations first and disproved all of them: plain misassociation (offline and on bbsim, the moved
            object is always named correctly), merging at close range (only within ~4 cm), a seed-vs-map
            measurement mismatch (0.0-1.0 cm per object, far under MOVE_M), and viewpoint drift while patrolling
            (20 passes, 0 dirty). 2) `demo_sim check` timed out at 450 s on beat 2 with 17 GB free, so the stall
            is not only the disk. 3) The sim arm removes an object from /sim/truth while it is in the hand, which
            is honest but breaks any sampler that assumes the object exists.

## h00 · web/landing + web/pages/seer · the leak is fixed and proven flat; the second renderer is a restructure, so it stands
Files:      web/pages/seer/spatial-viewer.js (release() also calls the object's own dispose(); the canvas gets an id
            and is sized from its host at once, not only when the resize observer first fires) — another session's
            file, changed on master's explicit authorisation. web/landing/tools/dev/framewatch.mjs (--exercise skips
            destructive controls; --press=LABEL opts one back in; canvas id and size are read when reporting, not at
            context-creation time, when they are not set yet).
Verified:   Per view, before: Voxels leaked 2 GL buffers a visit and Room map leaked 2 — both mount an InstancedMesh,
            whose instanceMatrix and instanceColor live OUTSIDE the geometry and are freed only by InstancedMesh's own
            dispose(). After: every view 0. Across three exercise rounds on /telemetry, buffers 499/499/499, textures
            9/9/9, programs 14/14/14. The canvas now reports as canvas#spatial-viewer 1065x458, not an anonymous
            300x150. The laser, measured by calling fireLaser() directly (it draws and makes no network call): worst
            frame 19 ms, median 16.7, zero frames over 30, and the same under reduced motion. It uses a 2-D canvas,
            so it adds no WebGL context. Page holds 60 fps.
Blocked on: master's call on the second renderer. Sharing is a restructure: a WebGLRenderer owns one canvas and these
            two draw in two places, so it needs one canvas spanning both with scissor-and-viewport, or a render target
            blitted to 2-D every frame. The context is already made lazily and only if a spatial view is opened.
Surprise:   The gate was pressing [mark fixed], which writes to Sentry. Nothing was ever sent, but only because the
            confirm-arm needs a second press within 4 s under a changed label and the clicker pressed each label once
            per round. A test that presses every button will eventually press one that means it; that is now filtered
            by default and the skipped labels are named in the output. Also worth keeping: the gate reported my own
            fix as not applied, because it snapshotted the canvas id at getContext time — which is before the line
            that sets it. A measurement taken at the wrong moment is the recurring bug of this whole session.

## h00 · elastic · the voxel cubes covered 0.3% of the cube; they now cover the room, and the keying is proved total
Files:      elastic/pipelines/room-voxels-keys.json (new: derives every voxel_key_lN prefix server-side),
            elastic/mappings/room-voxels.json (+voxel_key_l6, voxel_key_l7, index.default_pipeline),
            elastic/queries.py (VOXEL_LEVELS gains l6/l7; voxel_changes documents the cross-depth trap),
            elastic/tests/test_octree.py (new, 15 tests), elastic/tests/world.py (rungs() helper),
            elastic/NOTES.md. fake/scene_gen.py (voxels() now emits the floor) — not my folder; bbsim
            already had that floor and only the voxel path was missing it. Told web-64 and perception-02.
Measured:   Before, whole history: occupied extent 0.94 x 1.50 x 1.12 m inside an 8 m cube = 0.3% of it,
            and the page's default 1 m view drew SIX cubes totalling 6 m^3 to represent 483 L of occupancy
            — 8.0% fill, four of the six >= 97.6% air. Fill by rung: l3 8.0%, l4 20.3%, l5 30.9%,
            l6 56.1%, leaf 100%. Cause was not resolution: room.yaml declares only desk and shelf, and
            voxels() filled only those two surfaces plus object boxes. The union of the two zones is
            0.92 x 1.50 m and the measured occupancy was 0.94 x 1.50 — an exact match.
After:      Floor emitted under bbsim's own floor_bounds rule: per commit 1,828 -> 6,368 docs,
            6 -> 26 one-metre cells, 100 -> 350 at 25 cm, footprint 4.0 x 4.0 m. Backfilled into all six
            commits (25,350 docs, 0 errors); 31,555 docs now. 169/169 elastic tests pass, 7/7 demo beats
            pass, whole repo 1,429 passed.
Verified:   Keying is TOTAL — 50,000 random points in the cube all key to a cell that contains them,
            outside-the-cube returns None rather than a clamped wrong cell, and 0 of 31,555 docs lack a
            key, so nothing is being silently dropped. Depth NESTS — key(n+1) == key(n) + one digit over
            20,000 points, so today's voxel_key is byte-identical to tomorrow's voxel_key_l7 and a depth
            change does not invalidate history. Both are now tests, not comments.
Recommend:  OCTREE_LEVELS 7 -> 8 (3.125 cm leaf = BB's own 3 cm map; ~8k cells/commit, fill 41.6% -> 76%).
            Depth 9 is out twice over: VoxelGrid.occ is a dense (2**levels)**3 bool array = 134 MB per
            grid on a Pi, and scene_cloud's 1.5 cm point spacing cannot put MIN_PTS=3 in a 1.56 cm cell.
Blocked on: master. The flip needs .env AND room.git/room.yaml changed together — index_voxels() refuses
            to write when they disagree — plus a writer restart. room.yaml is a room-repo commit, not mine.
Surprise:   The cost ceiling is not Elasticsearch. Storage is 524 B/doc (~25 MB for six commits at
            3.125 cm) and every query the page makes runs in 31-94 ms. It is perception's dense occupancy
            array, which quadruples in memory per level. Also: "cover the whole 3D space" splits in two.
            The KEYING already covers all of it; filling the air with cubes would be 1.1 GB/commit at
            6.25 cm and would make the map useless — if every cell is occupied the robot cannot plan
            through it. Walls and a ceiling were deliberately NOT added: bbsim has no wall geometry, so
            unlike the floor they would be invented rather than propagated.

## h13 · cloud · the panel's job tells the truth, the runbook sentence parses, and a missing signal says so
Files:      bridge/caretaker.py (a dispatched job carries the dispatch; a moment that resolves to HEAD says
            what it found), bridge/intents.py ("put it back the way it was <time>"), roomctl/why.py (an
            absent odom_residual is stated), tests: bridge/test_intents.py, web/tests/test_housebot.py,
            tests/test_when_why.py (+6)
Verified:   bridge 71 · web 177 · telemetry 57 · when/why 14 green. Live on :8099 against real room.git:
            panel "where are my keys" -> job.executor housebot-edge, job.state dispatching, dispatched true;
            "put it back the way it was 2 hours ago" -> plan, ref_resolved 1a668ec, 0 ops, moment
            {when 2026-09-19T16:09-04:00, at 2026-09-19T00:37:43Z, how time, source elasticsearch},
            detail "the room already looks the way it did at …: nothing to move (commit 1a668ec)".
            Sentry: room-clean is ACTIVE and green (master freed the seat the user released); the feeder
            (scripts/room_clean_beat.py) checks in every 30 s against the real room only.
Blocked on: :8000 restart for the three panel-visible fixes (master). Andrew's edge drops the adapter's
            `simulated` flag, so a REAL run can only be recorded as unknown, never proved real — one line
            in his CaretakerService would fix that.
Surprise:   The panel had been dispatching all along; what looked like "no executor connected" was the job
            OBJECT still carrying the fields stamped when it was built, next to a dispatch that said it had
            been sent. Two fields of one answer disagreeing cost a session an hour of reading the wrong code,
            and would have sent the presenter to the object page mid-demo for no reason.

## h00 · web/pages/seer + web/landing · the two-context decision is written into the file, and the gate can now hold it
Files:      web/pages/seer/spatial-viewer.js (a header explaining WHY there are two WebGL contexts and why both real
            merges cost more than the problem — so the next reader does not "fix" it under time pressure);
            web/landing/tools/dev/framewatch.mjs (--accept=A,B).
Verified:   `node tools/dev/framewatch.mjs http://127.0.0.1:8000 /telemetry <out> --exercise --accept="one WebGL
            context,one context after use"` → PASS, exit 0: 60 fps, worst frame 21 ms, 0 frames over 30 ms in 15 s,
            buffers/textures/programs flat at 499/9/14 across three rounds, clean console. The accepted checks still
            print their true state as `known`, and if one starts passing the gate says to retire the exception.
Blocked on: nothing. Watching the idle worst frame; master wants it named again if it crosses 50 ms.
Surprise:   Having won the argument for the gate, I nearly made it useless: with the two-context decision settled, it
            failed on every run for a reason nobody was going to act on, and master's rule is that nothing ships until
            it passes. A gate that always fails is a gate people route around, so an accepted exception has to be
            first-class — recorded, still measured, still printed, and loud when it becomes retirable.

## h17 · perception/pointcloud · a candidate on a just-vacated pose is held: the caretaker stops inventing work
Files:      perception/bb_source.py (hold_ghosts, called from scan_into_bb; GHOST_M), perception/tests/test_bb_source.py (+2).
Verified:   perception/tests 348 passed, 12 skipped. The test master asked for: the lamp moves, its cells stay at
            the old pose, and nothing is minted — no `added`, exactly one modified file, the one that moved. It
            FAILS with the hold disabled (`added: 1`), checked by monkeypatching it out. A second test covers the
            shape web-64 hit on a clicked run: a phantom never carries a neighbour's name; that neighbour is
            carried as `unobserved` with its file byte-identical.
Blocked on: a sim demonstration. The sim's watch loop imported bb_source at startup, so it still runs the pre-fix
            code; gitspace-22 has been asked to restart that one window (theirs).
Surprise:   I could NOT reproduce a phantom on demand: 26 sampled passes across two sims, scanning one map into two
            repos with and without the hold, zero phantoms in both. It depends on whether the robot can see the
            vacated spot at that moment. The two sightings with evidence stand (one pending at 0.2 s in beat 2;
            2-4 for ~30 s in beat 3, fresh ids each pass; web-64's confirmed glasses_case_d04f:tidy-1), so the fix
            rests on its tests rather than a measured sim delta, and master has that in writing.
            Also: the proximity test has to be HORIZONTAL. My first version compared in 3-D and missed the lamp's
            own fragment, which sits at the table 17 cm below the lamp's committed centre.

## h22 · graph · the History tab replaces Health — the point-cloud commit graph, vertical, with lanes and a real object diff
Files:      web/pages/room-cloud.js (the `git log` rail rewritten: the railroad lane walk ported from
            web/landing/graph.js:57-75, the preview + object-diff panel, ↑/↓/Home/End, and the
            branch/checkout/add command line); web/landing/room-ink.css (the rail lays out IN FLOW
            inside the History panel; the horizontal-era overlay rules are gone, not left orphaned);
            web/objdiff.py (NEW — graph_api._ops lifted out so one implementation serves both repos);
            web/graph_api.py (_ops delegates to it; nothing else changed);
            web/scene_api.py (/history over the WHOLE DAG with parents+refs+branches+dirty, GET /diff,
            POST /branch, POST /checkout — and no merge anywhere); web/tests/test_scene_graph.py (NEW, 9).
            The Health→History tab swap itself (robot.html, robot.css, room-chat.css/.js, room-frame.js,
            the .system-status→.room-history rename) was done by master, not me — I did not touch it.
Chosen:     ONE graph on the page, in the History tab only. It no longer also floats over the 3D canvas:
            two copies of one graph is the confusion this was meant to end. Vertical, newest at the top,
            lanes across x — what `git log --graph` has always looked like. The rail lays out in flow and
            #room-history scrolls as one column, so the tab behaves like Agent and Settings rather than
            inventing a second scrollbar.
            Keys: ↑ = newer (upwards), ↓ = older, Home = the tip, End = the first scan — bound to the RAIL,
            because room-camera.js orbits the canvas with the arrows. `[` / `]` stay document-wide;
            they are muscle memory and they work from anywhere on the page.
Verified:   Live on http://127.0.0.1:8000/robot with headless Chrome, after master's tab swap.
            Tabs are Agent · Settings · History; details.system-status, #connection-list and the
            data-panel="system-status" button are gone; room-connections.js/.css are unmounted with the
            files still on disk. The panel renders 380×536 with the graph inside it (.git-log is
            position:static, height 494) — it was 380×42 before, because .git-log was still absolutely
            positioned from when it floated over the canvas and so contributed no height.
            THE LANE-1 DOTS NO LONGER SIT ON THE LABELS. --rail was hardcoded at 34px on .git-track,
            which beat the value JS set on .git-log, so the rows never widened for a second lane. It is
            now set on .git-track itself from the lane count actually in play (lanes × 22 + 12), and it
            tracks the SVG's own width so the two cannot diverge again. Measured both ways in the page:
            hallway-map (linear, 1 lane) --rail 34px = svg 34px, padding 38px, 11px clearance;
            hallway-test (branched, 2 lanes) --rail 56px = svg 56px, padding 60px, 13px clearance.
            Zero dot/text collisions, zero page errors, zero failed requests.
            Graph: 7 nodes newest-first, 2 lanes (cap_0021 and cap_0008 hang off the main line — a fork
            that was in the data all along and that the old single-lane rail drew as a straight line),
            6 edges, lane changes dashed.
            Navigation: Home→cap_1003, ↓↓→cap_0020, ↑→cap_0021, End→cap_0007 (and its 488,702-point
            cloud loads behind a token, so a fast scrub drops the stale fetch); `]` from document focus
            →cap_0008. Selecting previews; nothing on the rail writes.
            Commands: `merge movie-night` is answered, not sent — no run button, no request. `branch`
            on a committed node arms at 880 ms and is disabled+striped at 180 ms; a click while disabled
            and a double-click's second click both wrote nothing (0 non-GET requests across the run).
            On a node with no commit (cap_0008, a capture .ply that was never committed) the branch
            preview says so and withholds the button rather than quietly branching at HEAD.
            Object diff: "what this node changed — against cap_0020", 2 ADDED, both NEW with first_seen,
            grouped by zone. Phone 390px: panel 366×628, fits, 13px clearance, diff panel 324px, no
            horizontal page scroll, no errors.
            Tests: web/tests/test_scene_graph.py 9 + test_scene_history.py 6 + graph 21 green; full web
            suite 194 passed / 6 failed, and those 6 fail identically at HEAD (es_shared.shared is None:
            the elasticsearch package is not importable here). Nothing I touched is in them.
Blocked on: nothing.
Surprise:   A CSS custom property declared on a child silently beats one set on an ancestor, so the JS
            that computed the rail width had been running correctly and changing nothing all along —
            the dots moved with the lane count, the text never did. The fix is not to compute harder but
            to set the property on the element whose rule declares it.
            Also: `hallway-test`'s objects are two `unknown_*` boxes at `class: unknown`,
            `color: "#808080"`, in one commit, and `hallway-map` has no zones/ at all, so the honest
            diff on the real instances is thin and the panel shows it thin. Everything the panel can say
            is exercised against a scratch instance in the session scratchpad instead. A richer instance
            has to come from segmentation labelling the boxes, not from the panel.
            room-connections.js/.css are unmounted, not deleted: they are a working live-health view and
            re-mounting them on another page is a link tag and a script tag.

## h00 · web/landing · the gate was passing pages it had measured nothing on, whenever a stream was open
Files:      web/landing/tools/dev/framewatch.mjs (streams excluded from the in-flight count; "page settled" is a real
            check that fails the run).
Verified:   Diagnosed by the tele3d pane: a live page holds an endless multipart camera response and two SSE streams,
            so a naive in-flight count sits at a permanent 3, settle always timed out, every leak check printed
            INCONCLUSIVE — and the run still exited 0. A pass over nothing measured, on both demo pages whenever the
            robot is connected. Fixed with two defences, since enumerating stream types is the deny-list mistake
            again: text/event-stream and multipart/* stop counting when their headers land, and anything open past
            6 s stops counting regardless. Now /telemetry settles in 23 s and /robot in 14 s, both PASS on REAL
            counts (9/496/13 and 12/85/7, flat first-vs-last). Telemetry's heap reads 16.6 MB, measured for the
            first time on a page that had actually stopped moving. An unsettled page now FAILS: proved on a
            purpose-built page that allocates for ever — FAIL, exit code 1.
            Also verified for master, in real Chrome at 1600x1000: /robot?octree=1&commit=1a668ec0… comes up with
            "Show Elastic octree" CHECKED and the status line "4,970 buckets · leaf cells (3.13 cm) · 4,970 docs ·
            1a668ec0", matching the API; a2b27037 appears nowhere in the page text. No page errors.
Blocked on: nothing.
Surprise:   My proof that the new check fails a bad page printed "exit=0", and I nearly reported the fix as broken.
            I had piped the gate through grep and was reading grep's exit status instead of the gate's. That is the
            fifth time tonight the same shape of error has appeared — measuring the wrong thing, or the right thing
            at the wrong moment — and the first four were in code I was correcting for exactly that. The habit that
            caught it each time was cheap: before believing a number, ask what produced it.

## h00 · elastic · the page was captioning 6.25 cm cells as "3.13 cm" — my bug, caught by a screenshot
Files:      web/pages/room-voxels.js (leafDepth from the data, not the cube; ?commit= pin added).
The bug:    kind() took the leaf size from cube.levels, which the flip set to 8, while every indexed
            document is still 7 characters. So the status line read "leaf cells (3.13 cm)" over cells
            that are 6.25 cm. The GEOMETRY was never wrong — each cell's size comes from its own key
            through decode_prefix — only the caption, which is the worse of the two next to a
            presenter saying "3 centimetres". Now labels from the key length of the last real leaf
            loaded, so it says 6.25 cm today and 3.125 cm on a depth-8 commit with no code change.
            Edge case caught while fixing: on an aggregated rung every key is a prefix, so reading
            depth off it would report the rung's own size as the leaf's; only non-aggregated
            responses update it.
Also:       ?commit=<40 hex> now pins the snapshot — it did not exist; a commit could only reach the
            octree through the room:select-object event, so no link could pin one. Verified against
            the live server: the pinned URL returns snapshot_source "requested" and commit 1a668ec0,
            unpinned returns a2b27037 / latest_indexed.
Found:      The page was defaulting to a2b2703, whose commit message is literally
            Revert "live check: tidied — mug back, marker back, scissors away" — the revert of a
            test, on a branch that is NOT an ancestor of main HEAD (checked with merge-base, not by
            trusting the branch label). 1a668ec0 IS an ancestor, and is the commit the blame and
            time-travel beats already resolve to, so pinning there puts the 3D view on the same
            commit the narration is about.
Answered:   master asked whether the octree can be shown without the capture cloud. No: roomCloud
            exposes no visibility API, there is no control, AND the story commits have no cloud at
            all (`git ls-tree -r 1a668ec0` has no .ply; room-clouds documents are robot captures
            with no commit_sha). So the hallway splat on screen is the only cloud that exists and
            the two layers cannot be reconciled by selection. Recommended an opt-in ?cloud=0 in
            room-cloud.js (not my file, not touched), which changes no default.
Honest:     I could not load the page in a browser to confirm any of the display claims — headless
            Chrome cannot create a WebGL context here and the swiftshader fallback hung. The API
            half is verified live; the DOM half is read off the line that builds it. Said so.

## h00 · web/landing · the gate demanded a silence a live page can never give
Files:      web/landing/tools/dev/framewatch.mjs (network idle now means "nothing substantial is loading", not
            "no request exists").
Verified:   After the Seer changes landed (seer.js +12 KB, new intro-bugs.js, room-cloud.js, room-voxels.js) the gate
            FAILED /telemetry with "NEVER SETTLED in 60 s" while every count beneath it was stable — 9/9/9,
            496/496/496, heap 16.4 flat. That contradiction was the tell. Measured it: the page polls
            /api/robot/view/status and /api/robot/link about twice every 3 s, and its longest gap with NO request at
            all is 6.0 s, so a demand for 8 s of total silence could never be met and the gate could never pass a
            healthy page. Idle now means no request open longer than 1.5 s — a poll finishes in milliseconds, a point
            cloud does not. Re-ran /robot afterwards to confirm the burst-parsing case the network test existed for
            is still caught: settles at 14 s. Both pages PASS, exit 0: /telemetry 60 fps, worst 21 ms, flat
            9/496/13; /robot 60 fps, worst 27 ms, flat 12/85/7; no writes, clean consoles.
Blocked on: nothing. Watch armed on every page source.
Surprise:   Sixth instance tonight, third inside this tool, of the failure being my measurement rather than the thing
            measured — and the tell was identical every time: a FAIL sitting on top of numbers that all look fine is
            nearly always the measurement. Also worth recording: an earlier 86 ms worst frame on /telemetry did NOT
            reproduce across three further runs (19, no-output, 30 ms). Load average is above 9 with every session's
            Chrome running, so single-run frame numbers near the threshold need a second run before anyone acts.

## h00 · elastic · "pick up the trash" answered with a ceramic cup, and nothing reported a problem
Files:      elastic/queries.py (MIN_RELEVANCE, resolve_object gains confident/top_score,
            _obs_unresolved), elastic/tests/test_relevance_floor.py (new, 16 live tests),
            ANDREW-HANDOFF.md §2b.
The bug:    The user asked whether "pick up the trash" would work. There is no trash in the room.
            resolve_object returned cup_7e21 — a ceramic cup — margin 0.015, no error anywhere.
            "tidy up" returned the hammer at margin 0.002. A vector search has no "not found": it
            always returns a nearest neighbour, so the failure is not an exception, it is a
            confident wrong answer. An agent acting on it bins the cup, and Sentry never hears,
            because nothing thinks it failed. Worst possible shape for a robot.
Rejected:   Thresholding the fused score or the margin. They do NOT separate — "a glass of water"
            (absent) outscores "something to drink from" (present), and "the television remote"
            (absent) has exactly the margin of "the thing I cut paper with" (present). A cutoff
            there refuses real objects. Checked before building, not after.
Found:      The usable signal was already in the response. text_similarity_reranker's _score IS the
            rerank relevance, offset by ~1.0 — verified by calling the reranker directly and
            matching the numbers (keys 1.3154 vs raw 0.3260; trash 1.0288 vs 0.0002). My own
            docstring had been saying never to compare it with a threshold; over-cautious for a
            fixed model. No extra inference call needed.
Measured:   10 present phrasings 1.074–1.572, 9 absent 0.962–1.131. MIN_RELEVANCE = 1.05 sits in
            the gap: refuses every destructive phrasing tested, refuses nothing real.
Now:        "pick up the trash" -> NOT IN THE ROOM (nearest cup_7e21, 1.029, refused + reported).
            "tidy up" -> refused. "where are my keys" -> keys_7c2e 1.315, act. "the thing I cut
            paper with" -> scissors_9f3a 1.225, act.
Sentry:     An unconfident resolve calls obs.robot_failure("object_not_found", level="warning"),
            fingerprinted by kind so it groups as one issue, carrying the phrasing and the three
            nearest objects. No-op while the DSN is parked; live the moment it returns — and then
            it doubles as the list of objects the room should learn.
Honest:     A floor against absurdity, not a correctness proof — "the banana" still resolves to the
            plant at 1.118. Said so in the handoff rather than burying it: anything destructive
            should still confirm with the person. And `matches` stays populated when confident is
            False (useful in a search box, dangerous in a gripper), so a caller that ignores the
            flag is exactly as unsafe as before. A flag nobody checks is not safety.
Tests:      16 live, including one asserting DAYLIGHT on both sides of the floor rather than just
            that the threshold happens to hold — if that gap closes the fix needs a new shape.
            186 elastic tests, 7/7 beats.

## h00 · elastic · my relevance floor was measured somewhere other than where it is enforced
Files:      elastic/queries.py (MIN_RELEVANCE comment: search floor vs acting floor, both sets of
            numbers, do-not-collapse), elastic/tests/test_relevance_floor.py (now 24 tests, covers
            the unscoped production condition and separates NEAR_MISS from ABSENT).
The defect: I measured and tested MIN_RELEVANCE with branch="main" (13 objects). bridge/caretaker.py
            calls resolve_object(query, k=5) with NO branch — every branch, 14 objects, because a
            bowl_0c55 exists off main. Unscoped, "the trash" resolves to that bowl at 1.095 and
            PASSES my 1.05 floor. My 16 tests passed the whole time, on a condition the caller
            never uses. gitspace-d2 caught it by measuring the real call site.
            Same family as the caption bug and the four key-length readers: the fact was taken from
            a convenient place rather than the place it is used.
Measured:   Unscoped, 14 present and 15 wrong phrasings — worst present 1.126 ("something to write
            with" -> marker), best wrong 1.154 ("a bottle of water" -> mug). They OVERLAP by 0.028,
            so no single threshold both keeps every real object and rejects every absent one.
            1.05: 0 real refused, 5 wrong accepted. 1.20: 0 wrong accepted, 2 REAL refused
            ("something to drink from" 1.169, "something to write with" 1.126).
Decision:   Keep 1.05 for SEARCH, keep d2's RESOLVE_MIN_SCORE 1.20 for ACTING. Not redundancy —
            two consequences, two floors, and the comment says so and names the other so a later
            tidy-up does not delete one. A test now fails if the ranges ever separate, at which
            point one floor could replace two deliberately.
Flagged:    1.20 refuses "something to drink from" and "something to write with" — real objects,
            and exactly the vague phrasings the conversational beat shows off. Right trade for a
            gripper, but any demo script saying "put something to drink from on the shelf" must
            name the mug instead. Told master.
Honest:     Part of the overlap is labelling, not model error — "a bottle of water" -> a mug is a
            defensible answer. Those live in a separate NEAR_MISS list so nobody raises the floor
            until it refuses real objects in order to make a reasonable answer count as wrong.
            And these numbers move with the room: the bowl arriving lifted "the trash" from 1.029
            to 1.095. Both floors are measurements of a room, not constants.
Tests:      194 elastic, 7/7 beats.

## A born object needs two looks (commit-side guard)

One scan can invent an object that was never there: the map keeps an object's cells at the pose it has just moved
from, and a candidate appears there for a pass or two. A phantom that reaches `main` can never be put back —
every later pass reports it deleted, no tidy can fix it ("cannot apply hunk: not present in room"), and the room
stays dirty for good with the badge red and no route out. It cost a demo-night stall: the only thing keeping the
room red was a thing that never existed.

**Today (`roomctl/repo.py`, `Repo.commit(..., witnessed=False)`).** A commit that NOBODY WATCHED — a loop or a
script committing a scanned tree — holds back an object it is seeing for the first time, and takes it on the next
look. A person running `room commit` is itself the second look and is not second-guessed; `room init` has no HEAD
and every object in it is legitimately newborn, so neither is guarded. `room commit --unwitnessed` is how a script
opts in. The scope is deliberately narrow: widening it to every path breaks both of those cases, which is how it
was first written and why it was backed out.

**Better, when there is time.** Perception already knows how many passes it has seen a candidate for
(`perception/associate.py` holds the association state, and the watch loop applies the same two-fresh-passes rule
to a *change*). If a record carried an observation count, the commit path would simply refuse a count of one,
instead of keeping its own ledger in `.git/gitspace-births.json` and inferring "seen before" from having refused
it before. That is a clean boundary rather than a rewrite: perception counts, the commit path refuses. It wants
one field on the record (or a sidecar the scan writes), and it wants coordinating with the association-side hold
so that a REAL new object does not need four passes to appear — one hold, not two.

## h20 · perception/pointcloud · the failing arm-safety test is (a): a finer costmap, not a lost obstacle
Files:      tests/test_cli.py (test_reset_refuses_what_the_robot_cant_stand_close_enough_for rewritten
            around the REACH invariant).
Verified:   Measured both ways on the same scene rather than reading the diff. At 6.25 cm the scissors at
            (0.70, -0.01) has NO stance (166 of 180 candidates rejected on base_fits); at 3.125 cm the
            planner finds (1.18, -0.01). Obstacle cells 76 -> 150 over the same pedestal, and the pedestal
            samples equally solid in both (14/224) — nothing was lost, the cells just got finer, so the
            inflated boundary resolves to a nearer cell. So the refusal this test asserted was a costmap
            RESOLUTION artefact, not an arm limit, and the test asserted the artefact.
            It now asserts what actually keeps the robot safe: every `[robot] pick` is made from the
            `[robot] drive` before it, and that distance must be within ARM.r_max. The mug is still
            refused with its tally, and the run is still 1 of 3 with exit 1. tests/test_cli.py 20 passed;
            test_base_pose + test_executor_order 315 passed.
Blocked on: nothing, but see below — the margin question is robot/'s.
Surprise:   The stance the planner now picks is a knife edge: 0.4800 m to the scissors against r_max 0.48,
            and 0.2894 m of body clearance against INFLATE_M 0.28 — 9 mm. Both are placeholder numbers
            ("MEASURE" in roomctl.executor.ArmModel), so with a real arm this is equality, not margin.
            Doubling the costmap resolution moved the robot 3 cm closer to the table, which is exactly the
            kind of change that looks like a planner improvement and is really a tolerance being spent.

## h21 · perception/pointcloud · REACH_MARGIN: a stance at the end of the arm's numbers is refused
Files:      perception/costmap.py (REACH_MARGIN, a `reach_margin` filter in BASE_FILTERS and in
            solve_base_pose_why), perception/tests/test_costmap.py (+1, and the filter tally),
            perception/tests/test_bb_source.py (fixture leg 0.30 -> 0.18 m so a stance exists inside the
            margin; assertions against the margin), tests/test_cli.py (the safety test now pins the MARGIN).
Verified:   perception/tests 368 passed, 12 skipped; tests/test_cli + test_base_pose + test_executor_order
            + test_pr 344 passed. On the fake desk the scissors is refused again, and the tally SAYS why:
            "180 base poses sampled: 163 base fits, 3 reach margin, 4 ik, 10 path". Nothing is attempted,
            0 of 3, exit 1 — the behaviour the old test asserted, now for the right reason.
            The new costmap test pins the margin both ways: with it, the only free ring is refused and
            ik/line_of_sight/path are all zero (it got that far and no further); with REACH_MARGIN
            monkeypatched to 1.0 the same geometry yields a pose, and that pose is between 0.9*r_max and
            r_max — exactly what the margin stops.
Blocked on: nothing. When the arm is measured the margin travels WITH the measurement; the comment says so
            beside the constant, because a measured r_max still wants a margin.
Surprise:   Two of my own test fixtures were built so that the ONLY stance was at the very end of the
            reach — test_bb_source's table leg left just the outermost ring free. They passed for the same
            reason the demo did: the planner was spending the last millimetre. Shrinking the leg was the
            honest fix, not widening the assertion.

## h00 · elastic · fixed a duplicated constant in my own tool; reported two I should not touch
Fixed:      scripts/measure_relevance_floors.py hardcoded ACT_FLOOR = 1.20 with a comment saying
            "imported by eye". caretaker's is float(os.getenv("RESOLVE_MIN_SCORE", "1.20")) — env
            OVERRIDABLE — so the tool that checks the floors would have reported a band the system
            does not use, the moment anyone set that variable. Now imports MIN_ACT_SCORE from
            caretaker; verified it tracks an override (RESOLVE_MIN_SCORE=1.30 -> reads 1.30).
            Same disease I spent the night finding in other people's code, in a file I wrote an
            hour ago: a measurement tool that copies the constant it checks agrees with itself
            rather than with the system.
Reported:   (1) A real 500 on /api/agent/command, caught by Sentry — "KeyError: 'detail'",
            bridge/agent_api.py:242 in _outcome, where `return "REFUSED", r["detail"]` is an
            unconditional access. Any action kind not in the list, without a detail key, 500s the
            endpoint the whole demo runs through. One-line fix sent to master; NOT applied because
            the file has uncommitted changes from 25 minutes ago and master's standing instruction
            is to stop if someone is in it. Three neighbours in the same function have the same
            shape (r['job']['job_id'], r["question"], r['to_zone']) — named, not touched.
            (2) room-clouds `bounds` for cap_1003 look like they are in the camera frame, not the
            world frame: doc x ~= my z, doc y == my x negated almost exactly, and the doc claims
            z reaches -1.52, below the floor. Sent to perception-02 to check against the writer.
Verified:   The cube is NOT dropping real data — 466,742 of 466,849 points of cap_1003 sit inside
            the pinned 8 m cube; the 107 outside are just past the x=+4 edge. So "points not in
            boxes" on screen is the two-different-rooms problem, not a cube that is too small.
            demo_hybrid.py mug --save still regenerates: cup_7e21 #2, BM25 misses it, synthetic
            provenance banner intact. 194 elastic tests, 7/7 beats.
Standing:   Overshoot belongs at the planner, not in the index — costmap.py INFLATE_M 0.28 m,
            "Inflate ONCE, here". Padding stored voxels would give the gripper the base's margin
            and make diffs noisy. The one place we accidentally UNDER-shoot is z_med.

## h14 · cloud · three bands, a confirmation that is a real exchange, and a 500 that ran on every answer
Files:      bridge/caretaker.py (refuse < 1.11 ≤ ask < 1.20 ≤ act; `_ask_first`; tidy and restore_time
            resolve what they name), bridge/contract.py (`confirmed_object`), bridge/agent_api.py
            (a confirmation reaches the Intent; `_outcome` cannot raise; the grammar path consults the
            room before "unknown command"), scripts/intent_service.py (gen_ai spans; tidy is a PLACE),
            bridge/test_caretaker_safety.py (new, 16), .env (INTENT_URL)
Verified:   bridge 93 · web 207 · telemetry 57 green. Live on the real room, grammar-only (the public
            tier's condition): "pick up the trash" and "television remote" REFUSE naming the nearest;
            "something to drink from" (1.169) and "something to write with" (1.126) ASK — "I think you
            mean the mug on the desk, not the bowl — shall I point at it?"; "my keys" (1.454) ACTS;
            the yes (the same sentence + payload.object_id) acts, resolved_by id. One trace, three
            processes, 36 spans: /api/agent/command → gen_ai.chat gpt-5-mini → es.query → housebot.job
            → Andrew's edge (60bffe27cdfa4375888eaa27c9ad4b8a).
Blocked on: nothing of mine. The two floors are measurements of THIS room's objects, not constants:
            re-measure if the object set changes before the demo.
Surprise:   A vector search has no "not found", and the shape of that bug was not where anyone looked
            first: the dangerous path was `tidy`, which named an object and then acted on the WHOLE ROOM
            without ever asking the resolver. restore_time had it too and nobody had hit it. And the
            summariser that writes one line for the trace was doing r["detail"] unguarded — so an action
            kind it had not seen turned work that had already SUCCEEDED into a 500 on the demo's own
            endpoint. Sentry caught that one in production, which is the whole argument for the project.

## h22 · perception/pointcloud · beats 2 and 3 re-run on the fixed stack: the phantom no longer mints anything
Files:      docs/DEMO-RUNBOOK.md (beats 2 and 3, the phantom row and the stall row, all re-measured).
Verified:   01:30Z, on gitspace-22's restarted stack (association fix + reach margin live).
            Beat 3: eight of eight assertions green, including "the robot left lamp_2d9b where it was put"
            and "`main` now says the lamp lives there".
            Beat 2, three runs: one complete in 14.3 s (pending 3.2, confirmed tidy-1 8.1, IN THE ARM'S
            HAND 11.2, back at (0.42, 0.18) 12.8, verified 14.3); one where the tidy never started; one
            where the arm DID put it back but the badge never went green in `check`'s window. gitspace-22
            had 5 of 5 an hour earlier, so it is a flake and it is theirs.
            PHANTOMS, which is what master asked for: 3 samples out of ~20 across two runs, each an
            UNTRACKED object near the messed pose with a FRESH id every pass, so it can never accumulate
            the two passes a chore needs. None confirmed, nothing minted. Before the fix: 2-4 at a time
            for ~30 s in beat 3, and web-64's confirmed glasses_case_d04f:tidy-1 from a `mess mug_a1b2`.
Blocked on: nothing.
Surprise:   The hold covers one direction only, and the measurement showed the other. It fires when a
            record is matched somewhere ELSE and a candidate still sits where it left. Right after a mess
            the opposite happens: the record keeps its home cells (not yet carved) and the REAL object at
            its new pose is the untracked one. That resolves itself when the old cells clear, and the
            fresh id per pass is what stops it minting work in the meantime — accidental protection, not
            designed, and worth saying out loud rather than claiming the fix covers both.

## h28 · web · ?cloud=0 for the Elastic beat, the agent asks instead of guessing, and the job line stops printing "null"
Files:      web/pages/room-cloud.js: opt-in ?cloud=0 hides the SCANNED cloud and nothing else, plus
            window.roomCloud.cloudVisible so another module can ask instead of reaching into the scene; the
            caption no longer advertises points that are not drawn. web/pages/room-chat.js + room-chat.css and
            web/landing/graph.js: the bridge's new `confirm` action — question, candidate, runner-up, why, and
            two buttons. send() in both takes the confirm payload through unchanged with a FRESH request_id.
            web/landing/livemap.js: the nav snapshot is fetched only once a `nav` event says something is
            publishing. web/landing/dash.js: the job line read "tidy-9 · runningnull". web/API-FOR-PAGES.md.
Verified:   Chrome, both consoles, the bridge's exact shapes intercepted. Panel and graph console alike: the
            question costs ONE request; pressing No leaves it at one; pressing Yes sends a second with a
            DIFFERENT id and the payload {"object_id":"mug_a1b2","text":"point at the mug"} — result.yes, whole.
            No page errors either side. ?cloud=0: camera identical at [5.91, 6.62, 11.53] with and without, so
            the octree is framed the same; default and ?cloud=1 unchanged. Job line now reads
            "tidy-9 · running · tidy mug_a1b2 · 0 of 1" for the loop and "job_abc · grasping" for the executor.
            Crawl of /, /?info, /robot, /telemetry, /live, /capture, /replay, /object at 430 px: 54 internal
            links all 200, no overflow, no errors. web 207 green.
Blocked on: a restart of :8000 for the bridge's half (the grammar fix, `confirm`, the new fields). Everything
            of mine is static files and is already live.
Surprise:   1) `append(null)` stringifies to "null" and `append(x).className` reads a property of undefined —
            both pass a syntax check and only fail when run. Three of that family today (replaceChildren(null),
            append(null), append(...).className). 2) The graph console's agent block appears when a commit is
            picked from the "preview a moment" <select>, NOT by clicking the rail — a presenter clicking nodes
            and getting nothing would read as a broken console. 3) My first confirm harness keyed its reply off
            "is this the first request", so the second send returned the acted reply and there was never a
            second card to say yes to: the test could not see the thing it was written to check. Keying it off
            whether the payload carries object_id — what the server actually does — is the difference between a
            mock and a stand-in. 4) A commit with no INDEXED voxels shows an empty octree rather than an error,
            so a demo link pinned to the wrong sha fails silently.

## h23 · perception/pointcloud · the runbook's octree link: three silent failures, one field that catches them
Files:      docs/DEMO-RUNBOOK.md (the octree section, the graph-console trap T7, beat 4c, and #5 now that
            HEAD has a bin).
Verified:   Measured all three candidate commits through /api/voxels, and web-64 re-measured independently
            and got the same: 1a668ec0 requested/4,970 cells/ancestor of main (master's pin); a2b2703
            requested/5,827 cells but NOT on main (the live-check revert); HEAD 24c4b447 requested/no cells.
            So a pin can fail three ways without saying so — a short sha falls back, an unindexed sha draws
            an empty octree, and a misspelled parameter (`commit` for `commit_sha`) returns the latest
            snapshot and looks like a real answer. The runbook now names `snapshot_source` as the tell:
            "requested" means honoured, "latest_indexed" means not, and it is cheaper to read than
            counting cells.
            Also: room.git HEAD now carries `bin: {pose: [0.30, -0.75, 0.45]}`, so "no bin in room.yaml"
            is retired and removals plan; what stops them is reach. With r_max 0.48, a 0.28 m base and the
            0.9 margin, only objects within ~0.17 m of a table edge can be stood in front of at all.
Blocked on: nothing.
Surprise:   Both web-64 and I were caught by the same misspelled parameter, separately, and each of us
            "confirmed" a pin that had never been honoured — the API answered a different question
            convincingly. Their sentence for why their commit suggestion was wrong is the one to keep:
            the API answers what is INDEXED, never what belongs in the story.

## h24 · perception/pointcloud · beats 4b and 4c rehearsed for real; one line of the script corrected
Files:      docs/DEMO-RUNBOOK.md (4b and 4c now observed, T7 refined).
Verified:   On the 01:20:58Z build of :8000, which is the first to carry the no-match and ask work.
            4b: "pick up the trash" → ok false, no_match, "there is nothing in the room that matches
            'trash'; the nearest are bowl, plant and cup". 4c: "something to drink from" → kind confirm,
            score 1.169 (inside d2's 1.11-1.20 ask band), asking "I think you mean the mug on the desk,
            not the bowl — shall I point at it?" with the runner-up named. "where are my keys" scores
            1.454 and acts without asking, as the band predicts.
            CORRECTION to the script master passed on: "tidy up" does NOT refuse. It is a whole-room
            command and comes back kind jobs / as tidy, so 4b uses "pick up the trash" alone.
            T7 re-verified by web-64 after the restart and unchanged; their page copy now names the
            control. Added: the dropdown's first option is the current HEAD, so picking blind lands on
            "room: give the room a bin" rather than the story commit.
Blocked on: nothing. Two NOT REHEARSED marks remain in the document, both honest.
Surprise:   Every beat I could not rehearse earlier tonight was blocked by a process older than the fix,
            not by the fix being wrong — three times now (the panel dispatch, the refusal, the ask). The
            runbook is worth more when it records which BUILD a claim came from, so each of these now
            names the start time of the server it was measured against.

## h15 · cloud · a gate on the model path, so a live key can sit behind a public endpoint
Files:      bridge/intents.py (kill switch, per-caller and daily caps, length cap, counters),
            bridge/agent_api.py (the caller reaches the gate; `understanding` in /api/agent/bridge),
            scripts/intent_service.py (max_output_tokens, reasoning low, input cap, sanitised upstream
            errors, call counter), scripts/gcp_mirror.sh (ships the intent service — it was not in the
            tarball at all), .env.example, bridge/test_intents.py (+5)
Verified:   bridge 95 · web 207 green. Measured on the live model, not estimated:
              gpt-5-mini 687 in / 131 out tokens, 2.0 s per call (reasoning=low cut 240 -> 131 and 3.9 -> 2.0 s)
              gpt-5-nano was 6/6 correct too but spent 1,298 output tokens and 8.6 s — smaller is NOT cheaper
              ONE FULL DEMO RUN = 2 model calls; the grammar answers 7 of the runbook's 9 sentences
              worst case per call 675 in / 900 out (the caps) => ~$0.002; 300/day => ~$0.59/day worst case
            Closed-path test, live, with INTENT_OFF=1 and again with INTENT_DAILY_CAP=0: both model-needing
            sentences answer HTTP 200 from the grammar + resolver ("I know the mug on the desk, but not what
            you want done with it"; "there is nothing in the room that matches 'pick up the trash'"), and
            "where are my keys" still dispatches. No 429, no 500.
Blocked on: master's ship (key, systemd unit, INTENT_URL, cap) — and the user's yes with the cost in front
            of them. Nothing deployed by me.
Surprise:   The smallest model was the expensive one: nano reasoned 5x harder than mini for the same six
            answers. And the guard would have shipped into a box that could never run it — scripts/ was
            not in the deploy tarball, so the service it protects would have been missing entirely.

## h19 · robot · the "three broken subsystems" were one absent USB device
Files:      robot/RUNBOOK.md §7b (how to find it in three commands), §7c
What it was: camera.head.jpeg / slam.pose / mapping.voxels had NO WRITER for hours while 34 bbos daemons ran and the
            IMU published. The link session found the cause: /dev/shm/camera.log repeating "could not find camera
            'USB Camera' … reopening in 2s", and lsusb with no such device — the head stereo camera is physically off
            the bus. slam's engine runs on camera.head.rgb, and mapping on slam, so one unplugged camera reads from
            up here as three independent subsystems failing. A person reseats a cable; nothing to restart.
Surprise:   Every layer reported honestly and the truth was still invisible: our /capture said camera_unavailable,
            /healthz said slam false, the watcher said bbos_silent, the daemons said they were running — and none of
            it could say "the camera is unplugged", because nothing we own can see a USB bus. The one place that knew
            was a log file in /dev/shm that no dashboard reads. Worth remembering when a subsystem is "down": ask what
            it is downstream OF before debugging it.

## h19 · robot · robot/RUNBOOK.md: one section per problem, and the numbering is unique again
Files:      robot/RUNBOOK.md
What:       The link session and I had each written up the absent head camera, in the same file, as §7b and §10 — and
            the appends had left TWO sections numbered 9 ("the boot units" and "the bus voltage") with 7b/7c/8b sitting
            after 9. Merged into one §10 (their richer text: the exact camera.log line, the hub chain, what slam.log
            and mapping.log show) plus §10b for our retry, and renumbered: Sentry delivery is §11, the bus voltage
            §12. Nothing was deleted except the duplicate. The h19 entry above points at §7b / §7c: those are now
            §10 / §10b.
Surprise:   Not a surprise so much as a smell worth naming: a runbook is the thing you read at 4am with a robot in
            pieces, and it had grown two sections with the same number and two answers to the same question, in one
            evening, simply because three of us were appending to it honestly. Cheap to fix now, expensive at 4am.

## h29 · web · the history graph works and draws like VS Code's: a clickable commit list over a real lane rail
Files:      web/landing/graph.js, web/landing/graph.css. Also web/pages/telemetry.html (`data-seer-late`).
Verified:   WHAT OUR HISTORY ACTUALLY IS, measured before building anything: 8 commits, 1 root, 0 MERGES, and
            2 lanes — 1a668ec and b3691ea each have two children, so live-check and movie-night fork off the
            trunk and never return, and lane 1 is freed and reused between them. A rail was worth building;
            every merge drawn on it today would be imaginary.
            THE LIST: click a commit and it is selected · re-clicking KEEPS it selected · ArrowUp/Down walk it
            with the preview following · Home/End · cmd/ctrl/shift-click picks the second and the preview
            becomes a diff (12dd252 -> a2b2703) · refs as badges, newest first · role=listbox/option with
            aria-selected. The "preview a moment" dropdown is hidden but still in sync, so everything reading
            picker.value keeps working; its row now says "compare with" and appears only when comparing.
            THE RAIL: lanes from the `parents` the endpoint already returned and nothing had consumed; a dot
            on the commit's lane, straight lines for lanes passing through, curves where a lane is born or
            merges away, colour stable per lane. SVG — framewatch still counts ONE WebGL context, the hero's.
            MERGES tested against a STAND-IN, not claimed: a synthetic history shaped like the demo's, two
            --no-ff PR merges plus a still-open branch. Both draw as two parents joining one dot, the pr lane
            curves out of the merge and back in at its tip, the open branch has no line above it.
            GATE: framewatch /?info --exercise PASS — 60 fps, worst frame 18 ms, 0 frames over 30 ms in 15 s,
            no texture/buffer/program leak, clean console, 14 of 14 controls over 3 rounds. web 207 green.
Removed:    SELECTING A COMMIT NO LONGER SENDS A COMMAND. It used to preview the first verb through the
            bridge at once, which was harmless while the only way to select was a dropdown. A click selects
            now and the arrows walk the list, so holding ArrowDown would have POSTed to the bridge once per
            commit. The gate found it as "2 write(s) attempted and blocked: POST /api/agent/command" — not
            code reading. The command is filled in and one press away; the plan is still a read and running
            it is still armed separately. Master signed this off and asked that it not be debounced back.
Not done:   the filmstrip (cloudline.js) still has its own selection and is NOT wired to this one. The two
            read DIFFERENT REPOSITORIES — the filmstrip is the scene instance's git log, this list is
            room.git — so linking them would be the exact lie cloudline's own header warns about. The list
            is room.git's commits because room.git is what the console acts on.
Surprise:   1) At 430 px the ref badges collided with the relative date and truncated into two useless
            "origin…" chips eating the subject. Legible beat identical: on phones the date goes (the list is
            already in time order) and only LOCAL refs show — a branch or tag is what a person types.
            2) Two bugs only the SCREENSHOT caught, both invisible in the text dump: branch tips drew a line
            upward to nothing (a leftover unfinished condition, `g.hasChildAbove !== false`, always true),
            and the merge curve started at the row top instead of at the dot, drawing through it.
            3) The subject column had collapsed to 4 px while three remote refs took 406 px, because refs had
            a grid column of their own. Badges now sit inline with the subject the way VS Code draws them.

## h16 · cloud · the room gets a save file: one command to put it back between demos, and the ledger is why
Files:      scripts/demo_state.py (new), docs/DEMO-RUNBOOK.md §5.
Verified:   THE ROUND TRIP, on a clone first: save `tidy` (clean) · move the mug to x=0.05 uncommitted ·
            save `messy` · restore tidy -> mug 0.61, 0 dirty · restore messy -> mug 0.05, 1 dirty · restore
            tidy again -> 0.61, clean. Repeatable, both directions, the dirty tree carried exactly.
            THEN ON THE REAL ROOM: saved `tidy` (24c4b44, main, clean, 8 refs). Moved it to the first scan
            and saved `first-scan`; /api/object-map went 11 objects -> 12 with marker_c3d4 present. Restored
            `tidy`: 11 objects, marker gone, 0 uncommitted, and all 8 refs back at the same shas.
            THE POINT, and why `git reset --hard` is not enough: a job id is a hash of what the job would DO
            (command + target + HEAD + the room as scanned), so the second demo of the same sentence returns
            the FIRST job, replayed, and the robot does not move. Restore clears the room's ledger
            (~/.cache/gitspace/jobs/<sha1 of the room path>), so the next demo dispatches fresh.
            WHAT A STATE HOLDS: every ref, which branch HEAD was on, and the WORKING TREE — "the mug is out
            of place" lives in the tree, not in a commit, so a refs-only snapshot would restore a room the
            scan disagrees with.
Surprise:   1) An uncommitted change is invisible on /robot. /api/object-map resolves a REF and reads that
            commit's tree, by design (it is the mapping a voxel query is built from; deriving it from the
            index would be circular). So a state a judge can SEE has to move HEAD — which is why the pair
            on offer is first-scan <-> tidy, an object that is there and then is not, and not a tree edit.
            2) The room already contained the pair. marker_c3d4 and tool_4f2a are on the desk at e51a75a and
            gone by HEAD, so the visible flip needed no invented commit — only a ref that moves.
            3) `refs` counted refs/demo-backup/*, so a room restored to an 8-ref state read as 11 and looked
            like it had drifted from the state it had just been set to. Counted heads/tags/remotes instead.
Not done:   no button. A reset control on gitirl.health is a thing a stranger can press during judging, and
            it would need an auth gate to be worth having; the script is two words at a terminal.

## h00 · elastic · elastic/ was the least-instrumented folder in the repo; now it is on the trace
Audited:   Every Sentry capability, against what is actually configured and called, before adding
           anything. 17 integrations load at runtime; session tracking, logs and traces (1.0) all
           on; 18 of obs.py's 19 public helpers are wired (76 span call sites, 21 robot_failure,
           16 capture_scope). Sentry Crons already exists (obs.py capture_checkin). There was no
           shelf of unused SDK capability to switch on.
Confirmed: The SDK runs ON the Pi, not the laptop reporting about it — server_name=robot reports
           CPython 3.10.12 and no local interpreter matches (3.11.9 / 3.9.6 / 3.13). Four roles:
           robot (fastapi, on-device), link (flask, the edge), laptop, web. 36 issues / 254 events
           in 14 days; 880,768 spans and 105,275 transactions accepted in 7 days.
Pushed back: "as much telemetry as possible" is the wrong goal and the numbers say so. 11,537 spans
           and transactions are discarded by sample_rate BY DESIGN; only 13 were lost to
           buffer_overflow. More volume gets sampled at the same rate and raises the chance of
           losing something during judging. Also left profiling OFF for the robot: obs.py forces it
           to 0.0 because the robot balances on the same CPU its process runs on, and turning it on
           to look thorough could destabilise a balancing robot.
The gap:   elastic/ had 4 obs calls across 6 files — three of them just trace_fields() stamping ids
           — and NOT ONE span, against perception's 44 and web's 40. So the read path the whole
           demo runs through contributed nothing to the waterfall: a trace showed that an HTTP call
           to Elasticsearch happened, never which retriever ran or what it scored. My folder, my gap.
Fixed:     queries.py now carries spans (12 instrumentation sites, was 4), each a no-op when Sentry
           is off and unable to throw: elastic.search_objects (query, retriever shape, rerank model,
           hits, top score/object), elastic.resolve_object (confident, top_score, margin — the three
           numbers that decide whether a gripper moves), elastic.voxel_changes (rung, both commits,
           added/removed), elastic.esql (query text, rows), elastic.across (the Sentry <-> Elastic
           join itself, visible from inside Sentry).
Verified:  Not just "the code runs" — queried Sentry back and the spans are there:
           elastic.search_objects 4, elastic.resolve_object 4, elastic.esql 1 in the last hour.
           194 elastic tests, 7/7 beats.
Corrected: Twice, mid-investigation. Said Sentry was parked (it is live; I had tested without
           loading .env) and read "0 transactions" as no tracing (wrong dataset in my query — the
           spans dataset shows 880k).

## h00 · elastic · audited every Sentry product against the prize wording; the demo page had no browser SDK
Audited:   The prize asks for TWO products beyond error monitoring. All SIX have real data, checked
           against the Sentry API not against config: Tracing (266,048 spans on `room status`,
           distributed laptop->Pi), Profiling (249,226 spans carry a profile.id), Logs (11,862 HTTP
           entries + ES calls + camera fetches), Uptime (`gitspace web` -> gitirl.health/api/health,
           60s, active), AI agent monitoring (gen_ai.execute_tool 425, gen_ai.chat 388,
           gen_ai.invoke_agent 6), Session Replay (10 in 14 days, one 15 s before I looked). Plus
           Crons: room-clean active; watch-loop disabled AND muted — flagged, not changed.
Found:     web/pages/robot.html — the page a judge looks at — loaded NO browser SDK. Replay and
           browser tracing existed only on the landing page, so the demo recorded nothing and its
           /api calls never joined the browser trace. The landing module is self-contained: it
           reads /api/config for the DSN and resolves its vendor bundle relative to ITSELF
           (VENDOR = new URL('./vendor/sentry/', import.meta.url)), and both /sentry.js and the
           bundle serve 200 from the app origin — so it was one line.
Fixed:     One script tag in robot.html. 207 web tests pass, page serves 200, tag confirmed in the
           response. Canvas replay is manual-snapshot only and nothing on that page calls it, so
           the WebGL views are not captured frame by frame.
UNVERIFIED: I could NOT confirm a replay records from /robot — headless Chrome has no WebGL here and
           --dump-dom exits before Replay flushes; five polls found nothing. Saying so rather than
           claiming it, because this is exactly how the "3.13 cm" caption got past me. A human must
           open /robot once and check a replay appears.
Stories:   The prize's second half ("show how observability shaped what you built") is the strong
           part and it is all true: the KeyError: 'detail' 500 on /api/agent/command found and fixed
           tonight; obs.py's keep_alive + deeper queue added after ~1h of venue-wifi drops (6 errors,
           67 transactions, 524 spans); 60 ValueErrors in 60 seconds from python-dotenv returning an
           inline comment as part of an IP; and camera_unavailable/_recovered pairs showing the robot
           FLAPPING rather than simply down. Plus one decision NOT to instrument: profiling forced to
           0.0 on the robot because it balances on the same CPU its process runs on.

## h30 · web · the Sentry panel's scrollback is READ from Sentry, not remembered
Files:      web/sentry_client.py (recent_issues gained `state`), web/telemetry_api.py (live_issues and
            GET /api/telemetry/sentry/issues[?state=resolved]), web/API-FOR-PAGES.md.
Why:        another session was about to build "show past issues" as an in-memory record of issues seen to
            vanish. Two things wrong with that: a reload shows nothing, and what it shows is not past issues
            but "issues this tab happened to witness disappearing" — a section whose label would be a lie.
            Sentry already knows: recent_issues hardcoded `is:unresolved`, and `is:resolved` is the same call.
Verified:   against the live org on a scratch server (:8077, so :8000 was untouched):
            default -> state unresolved, watching true, 25 issues (robot: camera_unavailable, bbos_silent)
            ?state=resolved -> state resolved, watching false, 14 issues (Cancel 2 running task(s), Cron
            failure: room-clean) — a genuinely different list · ?state=bogus -> 422. web 207 green.
            The query is looked up from a two-entry map, never interpolated: a Sentry query is a search
            language and these are the only two searches this project makes.
            `state` is echoed on EVERY answer, not just the unconfigured one, so a panel drawing both lists
            can tell which it is holding — that turned out to matter within the hour (below).
Blocked on: a restart of :8000. It last came up 05:22:30Z, before this was on disk.
Surprise:   THE SILENT FALLBACK, again, and the sharpest instance yet. On the un-restarted :8000,
            `?state=resolved` returns the 25 UNRESOLVED issues with state:null and HTTP 200 — an unknown
            query parameter is ignored, and the old handler answers available:true with a full array. A page
            testing `available === true || issues.length > 0` would have rendered open issues under "Earlier
            · resolved in Sentry", dimmed and stripped of their buttons: live issues shown to a judge as
            done. The guard is `doc.state === 'resolved'`, which is exactly why the field is echoed.
            This is the same shape as the octree pin earlier today (`commit=` instead of `commit_sha=`: the
            pin ignored, the answer plausible, and I "confirmed" a link that had never been honoured). The
            rule both times: when a parameter is added, the RESPONSE must say it was honoured and the caller
            must check that field — never infer it from the payload looking right. HTTP treats an unknown
            parameter as no error at all.
