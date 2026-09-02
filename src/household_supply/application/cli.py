from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence, TextIO
import sys

from .json_api import PlanJsonApi, dump_json, parse_json_object
from .models import ApplicationRequestError
from .service import ApplicationMarketError, PlanApplicationService


def run_plan_cli(
    service: PlanApplicationService,
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="household-supply")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="build a procurement plan from JSON")
    plan_parser.add_argument(
        "--request",
        default="-",
        help="JSON request file; '-' reads stdin",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.request == "-":
        text = stdin.read()
    else:
        try:
            text = Path(args.request).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            stderr.write(f"request_read_error: {exc}\n")
            return 2

    try:
        payload = parse_json_object(text)
    except ApplicationRequestError as exc:
        stderr.write(f"invalid_request: {exc}\n")
        return 2

    response = PlanJsonApi(service).handle("POST", "/plans", payload)
    if response.status != 200:
        stderr.write(dump_json(response.body))
        return 3 if response.status == 502 else 2

    stdout.write(dump_json(response.body))
    return 0
