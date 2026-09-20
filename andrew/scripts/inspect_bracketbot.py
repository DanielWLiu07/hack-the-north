#!/usr/bin/env python3
"""Read confirmed BracketBot BBOS topics and print a safe JSON summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gitirl_agent.robot.bracketbot import BBOSObservationSource


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=1.0)
    arguments = parser.parse_args()

    with BBOSObservationSource(timeout_seconds=arguments.timeout) as source:
        print(json.dumps(source.read_snapshot().summary(), indent=2))


if __name__ == "__main__":
    main()
