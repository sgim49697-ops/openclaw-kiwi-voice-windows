# kiwi_live_approval_bridge.py - promote safe Kiwi live dry-run previews into approval queue.
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from e2e_dry_run import ROOT, payload_hash, write_approval_request


LOG_PATH = ROOT / ".debugloop" / "runs" / "kiwi-live-dry-run.jsonl"
QUEUE_ROOT = ROOT / ".debugloop" / "queue"
STATUSES = ("pending", "approved", "rejected")


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def iter_log_entries(path: Path, start_line: int = 0) -> list[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return []
    entries: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number <= start_line or not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                entries.append((line_number, data))
    return entries


def request_path(status: str, request_id: str) -> Path:
    return QUEUE_ROOT / status / f"{request_id}.json"


def existing_request_status(request_id: str) -> str | None:
    for status in STATUSES:
        if request_path(status, request_id).exists():
            return status
    return None


def canonical_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": request.get("version"),
        "requestId": request.get("requestId"),
        "source": request.get("source"),
        "riskTier": request.get("riskTier"),
        "action": request.get("action"),
        "params": request.get("params", {}),
    }


def validate_approval_request(
    request: Any,
    *,
    action: str,
    risk_tier: str,
    allow_existing: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(request, dict):
        return ["approvalRequest must be an object"]
    for key in ("version", "requestId", "source", "riskTier", "action", "params", "payloadHash", "status"):
        if key not in request:
            errors.append(f"missing required approval field: {key}")
    if errors:
        return errors
    if request.get("version") != 1:
        errors.append("approval version must be 1")
    if request.get("status") != "pending":
        errors.append("approval status must be pending")
    if request.get("source") != "voice-planner-dry-run":
        errors.append("approval source must be voice-planner-dry-run")
    if request.get("action") != action:
        errors.append(f"approval action must be {action}")
    if request.get("riskTier") != risk_tier:
        errors.append(f"approval riskTier must be {risk_tier}")
    if not isinstance(request.get("params"), dict):
        errors.append("approval params must be an object")
    expected_hash = request.get("payloadHash")
    actual_hash = payload_hash(canonical_payload(request))
    if expected_hash != actual_hash:
        errors.append("approval payloadHash mismatch")
    if not allow_existing and request.get("requestId") and existing_request_status(str(request["requestId"])):
        errors.append(f"approval request already exists in queue: {request['requestId']}")
    return errors


def event_preview(event: dict[str, Any]) -> dict[str, Any] | None:
    result = event.get("result")
    return result if isinstance(result, dict) else None


def find_matching_event(
    *,
    entries: list[tuple[int, dict[str, Any]]],
    action: str,
    risk_tier: str,
    transcript_contains: str | None,
    allow_existing: bool = False,
) -> tuple[int, dict[str, Any], dict[str, Any], list[str]] | None:
    for line_number, event in reversed(entries):
        if event.get("status") != "dry-run":
            continue
        if transcript_contains and transcript_contains not in str(event.get("message", "")):
            continue
        preview = event_preview(event)
        if not preview:
            continue
        errors: list[str] = []
        if preview.get("wouldExecute") is not False:
            errors.append("preview wouldExecute must be false")
        route = preview.get("route") if isinstance(preview.get("route"), dict) else {}
        if route.get("action") != action:
            errors.append(f"route action must be {action}")
        if route.get("riskTier") != risk_tier:
            errors.append(f"route riskTier must be {risk_tier}")
        request = preview.get("approvalRequest")
        errors.extend(
            validate_approval_request(
                request,
                action=action,
                risk_tier=risk_tier,
                allow_existing=allow_existing,
            )
        )
        if errors:
            continue
        return line_number, event, request, errors
    return None


def write_request(request: dict[str, Any]) -> Path:
    status = existing_request_status(str(request["requestId"]))
    if status:
        raise FileExistsError(f"approval request already exists in {status}: {request['requestId']}")
    return write_approval_request(request)


def command_status(args: argparse.Namespace) -> int:
    entries = iter_log_entries(args.log_path)
    match = find_matching_event(
        entries=entries,
        action=args.action,
        risk_tier=args.risk_tier,
        transcript_contains=args.transcript_contains,
        allow_existing=True,
    )
    response: dict[str, Any] = {
        "status": "ok",
        "logPath": relative(args.log_path),
        "lineCount": line_count(args.log_path),
        "matchingEvent": None,
    }
    if match:
        line_number, event, request, _errors = match
        response["matchingEvent"] = {
            "line": line_number,
            "message": event.get("message"),
            "requestId": request.get("requestId"),
            "action": request.get("action"),
            "riskTier": request.get("riskTier"),
            "queueStatus": existing_request_status(str(request.get("requestId"))),
        }
    print(json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_write_latest(args: argparse.Namespace) -> int:
    entries = iter_log_entries(args.log_path, start_line=args.start_line)
    match = find_matching_event(
        entries=entries,
        action=args.action,
        risk_tier=args.risk_tier,
        transcript_contains=args.transcript_contains,
    )
    if not match:
        print("kiwi live approval bridge error: no matching safe live dry-run approval found", file=sys.stderr)
        return 1
    line_number, event, request, _errors = match
    path = write_request(request)
    print(
        json.dumps(
            {
                "status": "written",
                "line": line_number,
                "message": event.get("message"),
                "requestId": request["requestId"],
                "approvalRequestPath": relative(path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_wait_write(args: argparse.Namespace) -> int:
    start_line = line_count(args.log_path) if args.start_line is None else args.start_line
    deadline = time.monotonic() + args.timeout
    while time.monotonic() <= deadline:
        entries = iter_log_entries(args.log_path, start_line=start_line)
        match = find_matching_event(
            entries=entries,
            action=args.action,
            risk_tier=args.risk_tier,
            transcript_contains=args.transcript_contains,
        )
        if match:
            line_number, event, request, _errors = match
            path = write_request(request)
            print(
                json.dumps(
                    {
                        "status": "written",
                        "line": line_number,
                        "message": event.get("message"),
                        "requestId": request["requestId"],
                        "approvalRequestPath": relative(path),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        time.sleep(args.poll_interval)
    print(
        json.dumps(
            {
                "status": "blocked",
                "reason": "timed out waiting for matching Kiwi live dry-run approval",
                "startLine": start_line,
                "lineCount": line_count(args.log_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 1


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--log-path", type=Path, default=LOG_PATH)
    parser.add_argument("--action", default="notify")
    parser.add_argument("--risk-tier", default="low")
    parser.add_argument("--transcript-contains", default="테스트 알림")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote safe Kiwi live dry-run approval previews into the queue.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Show latest matching live dry-run approval preview.")
    add_common_arguments(status)

    latest = subparsers.add_parser("write-latest", help="Write the latest matching preview to pending queue.")
    add_common_arguments(latest)
    latest.add_argument("--start-line", type=int, default=0)

    wait = subparsers.add_parser("wait-write", help="Wait for a new matching preview and write it to pending queue.")
    add_common_arguments(wait)
    wait.add_argument("--start-line", type=int)
    wait.add_argument("--timeout", type=int, default=180)
    wait.add_argument("--poll-interval", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        if args.command == "status":
            return command_status(args)
        if args.command == "write-latest":
            return command_write_latest(args)
        if args.command == "wait-write":
            return command_wait_write(args)
    except Exception as exc:
        print(f"kiwi live approval bridge error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
