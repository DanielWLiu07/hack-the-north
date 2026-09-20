import unittest

from src.gitirl_agent.planner.models import ActionType
from src.gitirl_agent.protocol.daniel import (
    DanielContractError,
    point_action_from_daniel_job,
    robot_actions_from_daniel_job,
    world_state_from_daniel,
)


class DanielContractTests(unittest.TestCase):
    contract = {
        "frame": "world_z_up",
        "units": {"position": "m", "yaw": "deg", "duration": "s"},
    }

    def test_state_normalizes_cloud_pose_without_axis_guessing(self):
        state = world_state_from_daniel(
            {
                **self.contract,
                "sha": "abc123",
                "objects": [
                    {
                        "object_id": "mug_a1b2",
                        "class": "mug",
                        "zone": "desk",
                        "pose": {"x": 0.42, "y": 0.18, "z": 0.76, "yaw": 15},
                        "extents": {"x": 0.12, "y": 0.09, "z": 0.11},
                    }
                ],
            }
        )
        mug = state.objects[0]
        self.assertEqual(mug.position, {"x": 0.42, "y": 0.18, "z": 0.76})
        self.assertEqual(mug.orientation, {"yaw": 15.0})
        self.assertEqual(state.metadata["coordinate_frame"], "world_z_up")
        self.assertEqual(mug.metadata["coordinate_frame"], "world_z_up")

    def test_moved_op_becomes_one_high_level_action(self):
        translated = robot_actions_from_daniel_job(
            {
                **self.contract,
                "job_id": "job_9a2f",
                "target": "deadbeef",
                "ops": [
                    {
                        "op": "moved",
                        "object_id": "box_A",
                        "class": "box",
                        "zone": "desk",
                        "from": {"x": 0.1, "y": 0.2, "z": 0.7, "yaw": 0},
                        "to": {"x": 0.4, "y": 0.2, "z": 0.7, "yaw": 15},
                    }
                ],
            }
        )
        self.assertFalse(translated.unsupported)
        action = translated.actions[0]
        self.assertEqual(action.action_type, ActionType.MOVE_OBJECT)
        self.assertEqual(action.request_id, "job_9a2f")
        self.assertEqual(action.target.position["x"], 0.4)
        self.assertEqual(action.metadata["yaw_unit"], "deg")

    def test_unsupported_and_malformed_ops_never_become_actions(self):
        translated = robot_actions_from_daniel_job(
            {
                **self.contract,
                "job_id": "job_1",
                "ops": [
                    {"op": "added", "object_id": "cup_1", "to": {}},
                    {
                        "op": "moved",
                        "object_id": "box_A",
                        "from": {"x": 0, "y": 0, "z": 0},
                        "to": {"x": 1, "y": 2},
                    },
                ],
            }
        )
        self.assertFalse(translated.actions)
        self.assertEqual(len(translated.unsupported), 2)

    def test_state_requires_complete_pose(self):
        with self.assertRaises(DanielContractError):
            world_state_from_daniel(
                {
                    **self.contract,
                    "objects": [
                        {"object_id": "box_A", "pose": {"x": 1, "y": 2}}
                    ],
                }
            )

    def test_point_job_becomes_point_action(self):
        action = point_action_from_daniel_job(
            {
                **self.contract,
                "job_id": "job_point",
                "command": "point",
                "object_id": "keys_7c2e",
                "target_pose": {"x": 0.8, "y": 0.3, "z": 0.9},
                "zone": "shelf",
            }
        )
        self.assertEqual(action.action_type, ActionType.POINT_AT_OBJECT)
        self.assertEqual(action.target.position["z"], 0.9)

    def test_unknown_coordinate_frame_is_rejected(self):
        with self.assertRaisesRegex(DanielContractError, "frame"):
            world_state_from_daniel(
                {**self.contract, "frame": "camera", "objects": []}
            )

    def test_unknown_units_are_rejected(self):
        with self.assertRaisesRegex(DanielContractError, "units.position"):
            robot_actions_from_daniel_job(
                {
                    **self.contract,
                    "units": {"position": "cm", "yaw": "deg", "duration": "s"},
                    "job_id": "job_bad_units",
                    "ops": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
