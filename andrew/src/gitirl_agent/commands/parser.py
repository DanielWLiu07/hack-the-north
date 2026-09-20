"""Deterministic intent parsing and the future parser seam."""

from __future__ import annotations

import re
from typing import Optional, Protocol

from src.gitirl_agent.commands.models import CommandType, GitIRLCommand


class IntentParser(Protocol):
    def parse(self, text: str, request_id: Optional[str] = None) -> GitIRLCommand:
        """Normalize user text without executing it."""


class DeterministicIntentParser:
    """Conservative parser for the initial unambiguous command phrases."""

    _RESTORE_PATTERNS = (
        re.compile(r"^restore\s+(?P<state>[\w-]+)$"),
        re.compile(r"^set my room back to\s+(?P<state>[\w-]+?)(?:\s+mode)?$"),
    )
    _COMMIT_PATTERNS = (
        re.compile(r"^commit\s+(?P<state>[\w-]+)$"),
        re.compile(r"^save this as\s+(?P<state>[\w-]+)$"),
    )
    _DIFF_PATTERN = re.compile(
        r"^(?:show(?: me)? (?:the )?)?diff(?:\s+(?P<state>[\w-]+))?$"
    )

    def parse(self, text: str, request_id: Optional[str] = None) -> GitIRLCommand:
        raw_text = text
        normalized = " ".join(text.strip().lower().split())
        if normalized.startswith("gitirl "):
            normalized = normalized[len("gitirl ") :]

        exact_commands = {
            "add": CommandType.ADD,
            "status": CommandType.STATUS,
            "diff": CommandType.DIFF,
            "show diff": CommandType.DIFF,
            "show me the diff": CommandType.DIFF,
            "log": CommandType.LOG,
            "show log": CommandType.LOG,
        }
        command_type = exact_commands.get(normalized)
        if command_type is not None:
            return self._command(command_type, raw_text, request_id)

        diff_match = self._DIFF_PATTERN.fullmatch(normalized)
        if diff_match:
            return self._command(
                CommandType.DIFF,
                raw_text,
                request_id,
                target_state=diff_match.group("state"),
            )

        for pattern in self._RESTORE_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match:
                return self._command(
                    CommandType.RESTORE,
                    raw_text,
                    request_id,
                    target_state=match.group("state"),
                )

        for pattern in self._COMMIT_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match:
                return self._command(
                    CommandType.COMMIT,
                    raw_text,
                    request_id,
                    target_state=match.group("state"),
                )

        return self._command(None, raw_text, request_id)

    @staticmethod
    def _command(
        command: Optional[CommandType],
        raw_text: str,
        request_id: Optional[str],
        target_state: Optional[str] = None,
    ) -> GitIRLCommand:
        kwargs = {
            "command": command,
            "target_state": target_state,
            "raw_text": raw_text,
        }
        if request_id is not None:
            kwargs["request_id"] = request_id
        return GitIRLCommand(**kwargs)


def parse_command(text: str, request_id: Optional[str] = None) -> GitIRLCommand:
    return DeterministicIntentParser().parse(text, request_id)


class FutureLLMIntentParser:
    """Placeholder for a future provider-backed implementation."""

    def parse(self, text: str, request_id: Optional[str] = None) -> GitIRLCommand:
        # TODO: Implement only after selecting an intent provider and defining
        # structured output. The result must still pass normal validation.
        raise NotImplementedError("LLM-backed intent parsing is not configured")
