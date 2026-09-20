import threading
import unittest

from src.gitirl_agent.planner.models import (
    ActionStatus,
    ActionType,
    NavigationTarget,
    RobotAction,
)
from src.gitirl_agent.robot.navigation import NavigationBackend


class FakeNavLink:
    def __init__(self, result=None, error=None):
        self.result = result or {"status": "reached", "distance_to_goal_m": 0.1}
        self.error = error
        self.calls = []

    def go(self, points, **kwargs):
        self.calls.append((points, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def nav_action(request_id="nav-1", **target_changes):
    values = {
        "frame": "slam_world",
        "x": 1.0,
        "y": -0.5,
        "yaw_rad": 0.25,
        "map_revision": "map-a",
        "tolerance_m": 0.35,
        "timeout_s": 45.0,
    }
    values.update(target_changes)
    return RobotAction(
        ActionType.NAVIGATE_TO_POSE,
        request_id,
        navigation_target=NavigationTarget(**values),
    )


class NavigationBackendTests(unittest.TestCase):
    def test_forces_exact_non_global_goal(self):
        nav = FakeNavLink()
        backend = NavigationBackend(nav, map_revision="map-a")

        result = backend.execute(nav_action())

        self.assertTrue(result.success)
        points, options = nav.calls[0]
        self.assertEqual(points, [(1.0, -0.5, 0.25)])
        self.assertEqual(options["timeout"], 45.0)
        self.assertIs(options["global_goal"], False)
        self.assertIs(options["exact"], True)
        self.assertIsInstance(options["cancel"], threading.Event)

    def test_rejects_wrong_frame_and_map_without_moving(self):
        nav = FakeNavLink()
        backend = NavigationBackend(nav, map_revision="map-a")

        wrong_frame = backend.execute(nav_action(frame="world"))
        wrong_map = backend.execute(nav_action("nav-2", map_revision="map-b"))

        self.assertTrue(wrong_frame.failed)
        self.assertTrue(wrong_map.failed)
        self.assertEqual(nav.calls, [])

    def test_checks_terminal_distance(self):
        nav = FakeNavLink({"status": "reached", "distance_to_goal_m": 0.5})
        result = NavigationBackend(nav, map_revision="map-a").execute(nav_action())

        self.assertTrue(result.failed)
        self.assertIn("0.500m", result.message)

    def test_transport_error_is_unknown_and_is_deduplicated(self):
        nav = FakeNavLink(error=RuntimeError("socket lost"))
        backend = NavigationBackend(nav, map_revision="map-a")
        action = nav_action()

        first = backend.execute(action)
        second = backend.execute(action)

        self.assertIs(first.status, ActionStatus.UNKNOWN)
        self.assertEqual(first, second)
        self.assertEqual(len(nav.calls), 1)

    def test_timeout_is_terminal_failure(self):
        nav = FakeNavLink(error=TimeoutError("deadline"))
        result = NavigationBackend(nav, map_revision="map-a").execute(nav_action())

        self.assertTrue(result.failed)
        self.assertIn("did not reach", result.message)

    def test_busy_request_is_rejected(self):
        class BlockingNavLink(FakeNavLink):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()
                self.release = threading.Event()

            def go(self, points, **kwargs):
                self.calls.append((points, kwargs))
                self.entered.set()
                self.release.wait(2)
                return self.result

        nav = BlockingNavLink()
        backend = NavigationBackend(nav, map_revision="map-a")
        holder = []
        worker = threading.Thread(target=lambda: holder.append(backend.execute(nav_action())))
        worker.start()
        self.assertTrue(nav.entered.wait(1))
        busy = backend.execute(nav_action("nav-2"))
        nav.release.set()
        worker.join(2)

        self.assertTrue(busy.failed)
        self.assertIn("busy", busy.message)
        self.assertTrue(holder[0].success)

    def test_cancel_sets_event_seen_by_navlink(self):
        class CancellingNavLink(FakeNavLink):
            def __init__(self):
                super().__init__()
                self.entered = threading.Event()

            def go(self, points, **kwargs):
                self.entered.set()
                event = kwargs["cancel"]
                self.assert_event(event)
                raise RuntimeError("cancelled by test")

            @staticmethod
            def assert_event(event):
                if not event.wait(2):
                    raise AssertionError("cancel event was not set")

        nav = CancellingNavLink()
        backend = NavigationBackend(nav, map_revision="map-a")
        holder = []
        worker = threading.Thread(target=lambda: holder.append(backend.execute(nav_action())))
        worker.start()
        self.assertTrue(nav.entered.wait(1))
        acknowledgement = backend.cancel("nav-1")
        worker.join(2)

        self.assertTrue(acknowledgement.success)
        self.assertTrue(holder[0].failed)
        self.assertIn("cancelled", holder[0].message)


if __name__ == "__main__":
    unittest.main()
