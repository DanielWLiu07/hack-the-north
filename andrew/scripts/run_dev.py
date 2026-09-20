#!/usr/bin/env python3
"""Run gitirl-agent through the local CLI or JSONL development boundary."""

from __future__ import annotations

import asyncio
import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

# Keep the repository runnable before packaging/install decisions are made.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gitirl_agent.commands.parser import DeterministicIntentParser
from src.gitirl_agent.commands.validation import validate_command
from src.gitirl_agent.orchestration.orchestrator import Orchestrator
from src.gitirl_agent.protocol.messages import (
    CommandResultPayload,
    ErrorPayload,
    MessageEnvelope,
    MessageType,
    ParsedCommandPayload,
    UserCommandPayload,
)
from src.gitirl_agent.protocol.serialization import (
    ProtocolMessageError,
    deserialize_message,
    serialize_message,
    utc_timestamp,
)
from src.gitirl_agent.robot.http_adapter import HTTPRobotAdapter
from src.gitirl_agent.robot.interface import RobotAdapter
from src.gitirl_agent.robot.mock import MockRobotAdapter
from src.gitirl_agent.state.models import ObjectState, WorldState
from src.gitirl_agent.state.store import InMemoryStateStore, JsonFileStateStore


class DevApplication:
    """Small development composition root; not a production robot service."""

    def __init__(self, robot: Optional[RobotAdapter] = None) -> None:
        desired = WorldState(
            objects=(ObjectState("box_A", label="box", position="X"),)
        )
        current = WorldState(
            objects=(ObjectState("box_A", label="box", position="Y"),)
        )
        state_file = os.environ.get("GITIRL_STATE_FILE")
        if state_file:
            store = JsonFileStateStore(Path(state_file))
        else:
            store = InMemoryStateStore()
            store.save("study", desired)
        self._parser = DeterministicIntentParser()
        self._orchestrator = Orchestrator(
            store,
            robot if robot is not None else MockRobotAdapter(current),
        )

    async def handle(
        self,
        message: MessageEnvelope,
    ) -> Sequence[MessageEnvelope]:
        if (
            message.type is not MessageType.USER_COMMAND
            or not isinstance(message.payload, UserCommandPayload)
        ):
            return (
                MessageEnvelope(
                    type=MessageType.ERROR,
                    request_id=message.request_id,
                    timestamp=utc_timestamp(),
                    payload=ErrorPayload(
                        code="unsupported_message_type",
                        message="Development service accepts user_command only",
                    ),
                ),
            )

        command = self._parser.parse(message.payload.text, message.request_id)
        validation = validate_command(command)
        if not validation.valid:
            return (
                MessageEnvelope(
                    type=MessageType.ERROR,
                    request_id=message.request_id,
                    timestamp=utc_timestamp(),
                    payload=ErrorPayload(
                        code=validation.errors[0].code,
                        message=validation.errors[0].message,
                    ),
                ),
            )

        parsed = MessageEnvelope(
            type=MessageType.PARSED_COMMAND,
            request_id=message.request_id,
            timestamp=utc_timestamp(),
            payload=ParsedCommandPayload(
                command=command.command,
                target_state=command.target_state,
                message=command.message,
            ),
        )
        result = self._orchestrator.execute_command(command)
        completed = MessageEnvelope(
            type=MessageType.COMMAND_RESULT,
            request_id=message.request_id,
            timestamp=utc_timestamp(),
            payload=CommandResultPayload(
                status=result.status.value,
                message=result.message,
                attempts=result.attempts,
                differences=result.differences,
            ),
        )
        return parsed, completed


async def run_cli(application: DevApplication) -> None:
    print("gitirl-agent local mode. Type a command, or 'quit' to exit.")
    while True:
        try:
            text = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.strip().lower() in {"quit", "exit"}:
            return

        incoming = MessageEnvelope(
            type=MessageType.USER_COMMAND,
            request_id=f"local-{utc_timestamp()}",
            timestamp=utc_timestamp(),
            payload=UserCommandPayload(text=text),
        )
        for response in await application.handle(incoming):
            print(serialize_message(response))


async def run_jsonl(application: DevApplication) -> None:
    """Read and write one protocol message per line, with no prompts."""

    while True:
        raw = await asyncio.to_thread(sys.stdin.readline)
        if raw == "":
            return
        if not raw.strip():
            continue
        try:
            incoming = deserialize_message(raw)
            responses = await application.handle(incoming)
        except ProtocolMessageError as error:
            responses = (
                MessageEnvelope(
                    type=MessageType.ERROR,
                    request_id="invalid",
                    timestamp=utc_timestamp(),
                    payload=ErrorPayload(
                        code="invalid_message",
                        message=str(error),
                    ),
                ),
            )
        for response in responses:
            print(serialize_message(response), flush=True)


async def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--jsonl",
        action="store_true",
        help="read protocol JSON from stdin and write responses to stdout",
    )
    arguments = argument_parser.parse_args()
    robot = HTTPRobotAdapter.from_environment()
    application = DevApplication(robot)
    if robot is not None:
        print("gitirl-agent: using robot HTTP API from GITIRL_ROBOT_BASE_URL")
    if arguments.jsonl:
        await run_jsonl(application)
        return
    await run_cli(application)


if __name__ == "__main__":
    asyncio.run(main())
