import unittest

from src.gitirl_agent.planner.models import (
    ActionResult,
    ActionStatus,
    ActionType,
    RobotAction,
)
from src.gitirl_agent.robot.bracketbot import (
    ARM_STATE_TOPICS,
    CAMERA_STATUS_TOPICS,
    CAMERA_TOPICS,
    BBOSObservationSource,
    BracketBotAdapter,
    BracketBotUnavailableError,
)
from src.gitirl_agent.state.models import ObjectState, WorldState


class FakeReader:
    def __init__(self, data, ready=True):
        self.data = data
        self._ready = ready
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.closed = True

    def ready(self):
        return self._ready


def records():
    result = {}
    for name, topic in CAMERA_TOPICS.items():
        payload = f"{name}-jpeg".encode()
        result[topic] = {
            "jpeg_len": len(payload),
            "jpeg": payload + b"ignored-capacity",
            "timestamp": "camera-time",
        }
    for topic in CAMERA_STATUS_TOPICS.values():
        result[topic] = {
            "streaming": 1,
            "fps": 30.0,
            "timestamp": "status-time",
        }
    for topic in ARM_STATE_TOPICS.values():
        result[topic] = {
            "pos": [0.0] * 8,
            "vel": [0.0] * 8,
            "timestamp": "arm-time",
        }
    return result


class StaticInterpreter:
    def interpret(self, snapshot):
        return WorldState(
            objects=(
                ObjectState(
                    "box_A",
                    label="box",
                    position="observed",
                    metadata={"camera_count": len(snapshot.camera_jpegs)},
                ),
            )
        )


class RecordingExecutor:
    def __init__(self):
        self.actions = []

    def execute(self, action):
        self.actions.append(action)
        return ActionResult(ActionStatus.SUCCESS, "robot stack completed action")


class BracketBotTests(unittest.TestCase):
    def test_reads_confirmed_bbos_topics(self):
        raw = records()
        readers = {}

        def factory(topic, keeptime=False):
            self.assertFalse(keeptime)
            readers[topic] = FakeReader(raw[topic])
            return readers[topic]

        with BBOSObservationSource(reader_factory=factory) as source:
            snapshot = source.read_snapshot()
            self.assertEqual(snapshot.camera_jpegs["head"], b"head-jpeg")
            self.assertEqual(snapshot.camera_status["left"]["fps"], 30.0)
            self.assertEqual(len(snapshot.arm_state["right"]["pos"]), 8)
            self.assertEqual(snapshot.summary()["cameras"]["right"]["jpeg_bytes"], 10)

        self.assertTrue(all(reader.closed for reader in readers.values()))

    def test_timeout_names_unavailable_topic(self):
        raw = records()

        def factory(topic, keeptime=False):
            return FakeReader(raw[topic], ready=topic != "camera.head.jpeg")

        source = BBOSObservationSource(
            timeout_seconds=0.001,
            poll_interval_seconds=0,
            reader_factory=factory,
        )
        with self.assertRaisesRegex(
            BracketBotUnavailableError, "camera.head.jpeg"
        ):
            source.read_snapshot()
        source.close()

    def test_adapter_keeps_semantics_and_execution_injected(self):
        raw = records()
        source = BBOSObservationSource(
            reader_factory=lambda topic, keeptime=False: FakeReader(raw[topic])
        )
        executor = RecordingExecutor()
        adapter = BracketBotAdapter(source, StaticInterpreter(), executor)

        observed = adapter.observe()
        action = RobotAction(
            action_type=ActionType.MOVE_OBJECT,
            request_id="request-1",
            object_id="box_A",
        )
        result = adapter.execute(action)

        self.assertEqual(observed.get("box_A").position, "observed")
        self.assertEqual(observed.get("box_A").metadata["camera_count"], 3)
        self.assertTrue(result.success)
        self.assertEqual(executor.actions, [action])
        adapter.close()


if __name__ == "__main__":
    unittest.main()
