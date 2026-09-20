import unittest

from src.gitirl_agent.commands.models import CommandType
from src.gitirl_agent.commands.parser import parse_command
from src.gitirl_agent.commands.validation import validate_command


class ParserTests(unittest.TestCase):
    def test_restore_study(self) -> None:
        command = parse_command("restore study", "request-1")

        self.assertIs(command.command, CommandType.RESTORE)
        self.assertEqual(command.target_state, "study")
        self.assertEqual(command.request_id, "request-1")

    def test_natural_restore_study(self) -> None:
        command = parse_command("set my room back to study", "request-2")

        self.assertIs(command.command, CommandType.RESTORE)
        self.assertEqual(command.target_state, "study")

    def test_raw_gitirl_commands_do_not_require_nlp(self) -> None:
        examples = (
            ("gitirl commit study", CommandType.COMMIT, "study"),
            ("gitirl diff study", CommandType.DIFF, "study"),
            ("gitirl restore study", CommandType.RESTORE, "study"),
            ("gitirl status", CommandType.STATUS, None),
        )

        for text, command_type, target_state in examples:
            with self.subTest(text=text):
                command = parse_command(text, "raw-command")
                self.assertIs(command.command, command_type)
                self.assertEqual(command.target_state, target_state)
                self.assertTrue(validate_command(command).valid)

    def test_invalid_command_returns_structured_error(self) -> None:
        result = validate_command(parse_command("tidy it somehow", "request-3"))

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, "unknown_command")
        self.assertEqual(result.errors[0].message, "Unknown or ambiguous command")


if __name__ == "__main__":
    unittest.main()
