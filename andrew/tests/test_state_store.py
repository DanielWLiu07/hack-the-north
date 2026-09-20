import json
import tempfile
import unittest
from pathlib import Path

from src.gitirl_agent.state.models import ObjectState, WorldState
from src.gitirl_agent.state.store import JsonFileStateStore


class JsonFileStateStoreTests(unittest.TestCase):
    def test_world_state_round_trips_through_json(self) -> None:
        state = WorldState(
            objects=(
                ObjectState(
                    object_id="box_A",
                    label="box",
                    position={"development_value": "X"},
                    relationships={"on": "desk"},
                    confidence=0.9,
                ),
            ),
            metadata={"fixture": True},
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "states.json"
            store = JsonFileStateStore(path)
            store.save("study", state)

            self.assertEqual(store.load("study"), state)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["version"], 1)
            self.assertIn("study", document["states"])


if __name__ == "__main__":
    unittest.main()
