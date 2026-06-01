# approval_queue.py - inspect external approval queue files without approving them.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = ROOT / ".debugloop" / "queue"
STATUSES = ("pending", "approved", "rejected")


def load_request(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return data


def request_files(status: str) -> list[Path]:
    directory = QUEUE_ROOT / status
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def summarize_request(path: Path) -> str:
    try:
        data = load_request(path)
    except Exception as exc:
        return f"{path.relative_to(ROOT)} | invalid | {exc}"

    request_id = data.get("requestId", path.stem)
    action = data.get("action", "<missing-action>")
    risk = data.get("riskTier", "<missing-risk>")
    status = data.get("status", path.parent.name)
    reason = " ".join(str(data.get("reason", "")).split())
    if len(reason) > 120:
        reason = reason[:117] + "..."
    return f"{path.relative_to(ROOT)} | {status} | {risk} | {action} | {request_id} | {reason}"


def print_queue(status: str) -> int:
    statuses = STATUSES if status == "all" else (status,)
    printed = 0
    for item in statuses:
        files = request_files(item)
        if status == "all":
            print(f"[{item}] {len(files)}")
        for path in files:
            print(summarize_request(path))
            printed += 1
    if printed == 0 and status != "all":
        print(f"No {status} approval requests.")
    return 0


def print_status() -> int:
    for status in STATUSES:
        print(f"{status}: {len(request_files(status))}")
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect OpenClaw debug approval queue files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue", help="Print approval requests.")
    queue.add_argument("--status", choices=("all", *STATUSES), default="pending")

    subparsers.add_parser("status", help="Print approval queue counts.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.command == "queue":
        return print_queue(args.status)
    if args.command == "status":
        return print_status()
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
