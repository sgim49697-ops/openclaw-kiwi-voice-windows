# approval_queue.py - manage external approval queue files without self-approval.
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = ROOT / ".debugloop" / "queue"
STATUSES = ("pending", "approved", "rejected")
APPROVAL_METHODS = ("manual", "telegram", "owner_voice", "openclaw_approval", "github_pr_review", "local_file")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def request_path(status: str, request_id: str) -> Path:
    return QUEUE_ROOT / status / f"{request_id}.json"


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def write_request(path: Path, request: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def summarize_request(path: Path) -> str:
    try:
        data = load_request(path)
    except Exception as exc:
        return f"{relative(path)} | invalid | {exc}"

    request_id = data.get("requestId", path.stem)
    action = data.get("action", "<missing-action>")
    risk = data.get("riskTier", "<missing-risk>")
    status = data.get("status", path.parent.name)
    reason = " ".join(str(data.get("reason", "")).split())
    if len(reason) > 120:
        reason = reason[:117] + "..."
    return f"{relative(path)} | {status} | {risk} | {action} | {request_id} | {reason}"


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


def print_show(request_id: str, status: str) -> int:
    statuses = STATUSES if status == "all" else (status,)
    for item in statuses:
        path = request_path(item, request_id)
        if path.exists():
            print(json.dumps(load_request(path), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    print(f"approval request not found: {request_id}", file=sys.stderr)
    return 1


def assert_confirmed(request_id: str, confirm_request_id: str) -> None:
    if request_id != confirm_request_id:
        raise ValueError("confirm-request-id must match request-id")


def assert_pending_request(request: dict, request_id: str) -> None:
    if request.get("requestId") != request_id:
        raise ValueError("requestId in file does not match requested id")
    if request.get("status") != "pending":
        raise ValueError("only pending requests can be transitioned")
    if "payloadHash" not in request:
        raise ValueError("payloadHash is required")


def transition_request(
    *,
    request_id: str,
    target_status: str,
    confirm_request_id: str,
    method: str,
    actor: str,
    reason: str | None,
) -> int:
    assert_confirmed(request_id, confirm_request_id)
    source = request_path("pending", request_id)
    if not source.exists():
        raise FileNotFoundError(f"pending request not found: {relative(source)}")
    target = request_path(target_status, request_id)
    if target.exists():
        raise FileExistsError(f"target request already exists: {relative(target)}")

    request = load_request(source)
    assert_pending_request(request, request_id)
    timestamp = now_iso()
    request["status"] = target_status
    request["approvalMethod"] = method
    if target_status == "approved":
        request["approvedBy"] = actor
        request["approvedAt"] = timestamp
    else:
        request["rejectedBy"] = actor
        request["rejectedAt"] = timestamp
    if reason:
        request[f"{target_status}Reason"] = reason

    write_request(target, request)
    source.unlink()
    print(f"{request_id}: pending -> {target_status} ({method}, {actor})")
    print(relative(target))
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect OpenClaw debug approval queue files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    queue = subparsers.add_parser("queue", help="Print approval requests.")
    queue.add_argument("--status", choices=("all", *STATUSES), default="pending")

    subparsers.add_parser("status", help="Print approval queue counts.")

    show = subparsers.add_parser("show", help="Print one approval request JSON.")
    show.add_argument("--request-id", required=True)
    show.add_argument("--status", choices=("all", *STATUSES), default="all")

    approve = subparsers.add_parser("approve", help="Move a pending request to approved with explicit owner confirmation.")
    approve.add_argument("--request-id", required=True)
    approve.add_argument("--confirm-request-id", required=True)
    approve.add_argument("--method", choices=APPROVAL_METHODS, required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--reason")

    reject = subparsers.add_parser("reject", help="Move a pending request to rejected with explicit owner confirmation.")
    reject.add_argument("--request-id", required=True)
    reject.add_argument("--confirm-request-id", required=True)
    reject.add_argument("--method", choices=APPROVAL_METHODS, required=True)
    reject.add_argument("--rejected-by", required=True)
    reject.add_argument("--reason")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        if args.command == "queue":
            return print_queue(args.status)
        if args.command == "status":
            return print_status()
        if args.command == "show":
            return print_show(args.request_id, args.status)
        if args.command == "approve":
            return transition_request(
                request_id=args.request_id,
                target_status="approved",
                confirm_request_id=args.confirm_request_id,
                method=args.method,
                actor=args.approved_by,
                reason=args.reason,
            )
        if args.command == "reject":
            return transition_request(
                request_id=args.request_id,
                target_status="rejected",
                confirm_request_id=args.confirm_request_id,
                method=args.method,
                actor=args.rejected_by,
                reason=args.reason,
            )
    except Exception as exc:
        print(f"approval queue error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
