import json
import unittest

from src.gitirl_agent.commands.models import CommandType
from src.gitirl_agent.protocol.messages import (
    MessageEnvelope,
    MessageType,
    ParsedCommandPayload,
    UserCommandPayload,
)
from src.gitirl_agent.protocol.serialization import (
    deserialize_message,
    serialize_message,
)


class ProtocolTests(unittest.TestCase):
    def test_provisional_user_command_deserializes(self) -> None:
        raw = json.dumps(
            {
                "type": "user_command",
                "request_id": "abc123",
                "payload": {"text": "restore study"},
            }
        )

        message = deserialize_message(raw)

        self.assertIs(message.type, MessageType.USER_COMMAND)
        self.assertEqual(message.request_id, "abc123")
        self.assertIsInstance(message.payload, UserCommandPayload)
        self.assertEqual(message.payload.text, "restore study")

    def test_parsed_command_serializes(self) -> None:
        message = MessageEnvelope(
            type=MessageType.PARSED_COMMAND,
            request_id="abc123",
            payload=ParsedCommandPayload(
                command=CommandType.RESTORE,
                target_state="study",
            ),
        )

        value = json.loads(serialize_message(message))

        self.assertEqual(value["type"], "parsed_command")
        self.assertEqual(value["request_id"], "abc123")
        self.assertEqual(
            value["payload"],
            {"command": "restore", "target_state": "study"},
        )


if __name__ == "__main__":
    unittest.main()
