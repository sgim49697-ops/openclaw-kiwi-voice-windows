# e2e_approved_runner.py - run externally approved OpenClaw E2E requests only.
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from e2e_dry_run import DEFAULT_BROWSER_PROFILE, DEFAULT_BROWSER_URL, payload_hash


ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = ROOT / ".debugloop" / "queue"
APPROVED_DIR = QUEUE_ROOT / "approved"
DEFAULT_LOG_PATH = ROOT / ".debugloop" / "runs" / "e2e-approved-runs.jsonl"
EXECUTED_DIR = ROOT / ".debugloop" / "runs" / "e2e-approved-executed"
DISPATCHER_PATH = r"C:\OpenClawActions\Invoke-OpenClawAction.ps1"
DISPATCHER_ACTIONS = {
    "notify",
    "open_url_readonly",
    "open_vscode_codex_plan",
    "open_app_allowlisted",
    "run_task_recipe",
}
BROWSER_ACTIONS = {
    "browser_read",
    "browser_interact",
}
DENIED_RISK_TIERS = {"critical"}
DISPATCHER_APPROVAL_METHODS = {"voice", "telegram", "manual"}
LIVE_ACTIONS = {"notify"}
LIVE_APPROVAL_METHODS = {"manual", "telegram"}
LIVE_RISK_TIERS = {"low"}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def short_output(output: str, limit: int = 1400) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def decode_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return output.decode("utf-8", errors="replace")


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_command(name: str, command: Sequence[str], timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=command_env(),
        )
        output = decode_output(completed.stdout).strip()
        returncode = completed.returncode
    except FileNotFoundError as exc:
        output = str(exc)
        returncode = 127
    except subprocess.TimeoutExpired as exc:
        output = decode_output(exc.stdout).strip() or "command timed out"
        returncode = 124

    return {
        "name": name,
        "command": list(command),
        "returncode": returncode,
        "status": "ok" if returncode == 0 else "blocked",
        "durationMs": int((time.monotonic() - started) * 1000),
        "detail": short_output(output),
    }


def load_request(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{relative(path)}: top-level JSON value must be an object")
    return data


def approval_files() -> list[Path]:
    if not APPROVED_DIR.exists():
        return []
    return sorted(path for path in APPROVED_DIR.glob("*.json") if path.is_file())


def selected_files(args: argparse.Namespace) -> list[Path]:
    files = approval_files()
    if args.request_id:
        path = APPROVED_DIR / f"{args.request_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"approved request not found: {relative(path)}")
        return [path]
    if args.all:
        return files
    if len(files) == 1:
        return files
    if not files:
        return []
    raise ValueError("Multiple approved requests found; pass --request-id or --all.")


def canonical_payload(request: dict) -> dict:
    return {
        "version": request.get("version"),
        "requestId": request.get("requestId"),
        "source": request.get("source"),
        "riskTier": request.get("riskTier"),
        "action": request.get("action"),
        "params": request.get("params", {}),
    }


def assert_payload_hash(request: dict) -> None:
    expected = request.get("payloadHash")
    if not isinstance(expected, str):
        raise ValueError("payloadHash is required")
    actual = payload_hash(canonical_payload(request))
    if expected != actual:
        raise ValueError("payloadHash mismatch")


def validate_request(request: dict) -> None:
    for key in (
        "version",
        "requestId",
        "source",
        "riskTier",
        "action",
        "params",
        "payloadHash",
        "status",
        "approvalMethod",
        "approvedBy",
        "approvedAt",
    ):
        if key not in request:
            raise ValueError(f"missing required field: {key}")
    if request["version"] != 1:
        raise ValueError(f"unsupported request version: {request['version']}")
    if request["status"] != "approved":
        raise ValueError("request status must be approved")
    if request["riskTier"] in DENIED_RISK_TIERS:
        raise ValueError("critical approved E2E requests are denied")
    action = request["action"]
    if action not in DISPATCHER_ACTIONS | BROWSER_ACTIONS:
        raise ValueError(f"unknown or denied action: {action}")
    if not isinstance(request["params"], dict):
        raise ValueError("params must be an object")
    assert_payload_hash(request)


def validate_live_request(request: dict, confirm_request_id: str | None) -> None:
    request_id = str(request.get("requestId", ""))
    if not confirm_request_id:
        raise ValueError("live execution requires --confirm-request-id")
    if confirm_request_id != request_id:
        raise ValueError("confirm-request-id must match request-id")
    if request.get("action") not in LIVE_ACTIONS:
        raise ValueError("v7.5 live execution is limited to notify")
    if request.get("riskTier") not in LIVE_RISK_TIERS:
        raise ValueError("v7.5 live execution requires low risk")
    if request.get("approvalMethod") not in LIVE_APPROVAL_METHODS:
        raise ValueError("v7.5 live execution requires manual or telegram approval")


def dispatcher_approval_method(request: dict) -> str:
    method = str(request.get("approvalMethod", "manual"))
    return method if method in DISPATCHER_APPROVAL_METHODS else "manual"


def approved_dispatcher_payload(request: dict) -> dict:
    return {
        **canonical_payload(request),
        "approvedByUser": True,
        "approvalMethod": dispatcher_approval_method(request),
        "payloadHash": request["payloadHash"],
    }


def base64url_json(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def safe_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("browser URL must be http or https")
    return value


def browser_command(profile: str, *args: str) -> list[str]:
    return ["openclaw", "browser", "--browser-profile", profile, *args]


def execute_dispatcher(request: dict, dry_run: bool) -> list[dict]:
    payload = approved_dispatcher_payload(request)
    encoded = base64url_json(payload)
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        DISPATCHER_PATH,
        "-RequestJsonBase64",
        encoded,
    ]
    if dry_run:
        return [
            {
                "name": "dispatcher",
                "command": command[:-1] + ["<base64url-json>"],
                "returncode": 0,
                "status": "dry_run",
                "durationMs": 0,
                "detail": "approved dispatcher command not executed by --dry-run",
            }
        ]
    return [run_command("dispatcher", command, timeout=180)]


def execute_browser_read(request: dict, dry_run: bool) -> list[dict]:
    params = request["params"]
    profile = params.get("profile", DEFAULT_BROWSER_PROFILE)
    if profile != DEFAULT_BROWSER_PROFILE:
        raise ValueError(f"browser profile must be {DEFAULT_BROWSER_PROFILE}")
    url = safe_url(str(params.get("url", DEFAULT_BROWSER_URL)))
    commands = [
        ("browser_status", browser_command(profile, "status"), 30),
        ("browser_open", browser_command(profile, "open", url), 45),
        ("browser_snapshot", browser_command(profile, "snapshot", "--format", "aria", "--limit", "200"), 45),
        ("browser_screenshot", browser_command(profile, "screenshot"), 45),
    ]
    if dry_run:
        return [
            {
                "name": name,
                "command": command,
                "returncode": 0,
                "status": "dry_run",
                "durationMs": 0,
                "detail": "approved browser read command not executed by --dry-run",
            }
            for name, command, _timeout in commands
        ]
    results = []
    for name, command, timeout in commands:
        result = run_command(name, command, timeout=timeout)
        results.append(result)
        if result["returncode"] != 0:
            break
    return results


def execute_browser_interact(request: dict, dry_run: bool) -> list[dict]:
    params = request["params"]
    profile = params.get("profile", DEFAULT_BROWSER_PROFILE)
    if profile != DEFAULT_BROWSER_PROFILE:
        raise ValueError(f"browser profile must be {DEFAULT_BROWSER_PROFILE}")
    command = ["python3", "scripts/wsl/browser_interact_probe.py", "--profile", profile, "--no-write-log"]
    if dry_run:
        return [
            {
                "name": "browser_interact_probe",
                "command": command,
                "returncode": 0,
                "status": "dry_run",
                "durationMs": 0,
                "detail": "approved safe browser interact fixture not executed by --dry-run",
            }
        ]
    return [run_command("browser_interact_probe", command, timeout=240)]


def execute_request(request: dict, dry_run: bool) -> list[dict]:
    action = request["action"]
    if action in DISPATCHER_ACTIONS:
        return execute_dispatcher(request, dry_run=dry_run)
    if action == "browser_read":
        return execute_browser_read(request, dry_run=dry_run)
    if action == "browser_interact":
        return execute_browser_interact(request, dry_run=dry_run)
    raise ValueError(f"unknown or denied action: {action}")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def executed_path(request_id: str) -> Path:
    return EXECUTED_DIR / f"{request_id}.json"


def write_executed_record(record: dict) -> None:
    path = executed_path(record["requestId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_file(path: Path, execute_live: bool, confirm_request_id: str | None, force: bool) -> dict:
    dry_run = not execute_live
    record = {
        "timestamp": now_iso(),
        "mode": "approved_e2e_runner",
        "requestPath": relative(path),
        "requestId": path.stem,
        "status": "blocked",
        "dryRun": dry_run,
        "skipped": False,
        "action": None,
        "riskTier": None,
        "steps": [],
        "errors": [],
    }
    try:
        request = load_request(path)
        validate_request(request)
        record["requestId"] = request["requestId"]
        record["action"] = request["action"]
        record["riskTier"] = request["riskTier"]
        if execute_live:
            validate_live_request(request, confirm_request_id)
        already_executed = executed_path(record["requestId"])
        if already_executed.exists() and execute_live and not force:
            record["status"] = "ok"
            record["skipped"] = True
            record["skipReason"] = f"already executed: {relative(already_executed)}"
            return record
        record["steps"] = execute_request(request, dry_run=dry_run)
        record["status"] = "ok" if all(step["status"] in {"ok", "dry_run"} for step in record["steps"]) else "blocked"
        if record["status"] == "ok" and execute_live:
            write_executed_record(record)
    except Exception as exc:
        record["errors"].append(str(exc))
    return record


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"requestId: {record['requestId']}")
    print(f"action: {record.get('action')}")
    if record.get("skipped"):
        print(f"skipped: {record.get('skipReason')}")
    if record.get("errors"):
        for error in record["errors"]:
            print(f"error: {error}")
    for step in record.get("steps", []):
        print(f"- {step['name']}: {step['status']} ({step['returncode']})")
        if step.get("detail"):
            print(f"  detail: {step['detail']}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run externally approved OpenClaw E2E requests.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--request-id", help="Approved request id to run.")
    group.add_argument("--all", action="store_true", help="Run all approved requests.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Validate and print commands without executing them. This is the default.")
    mode.add_argument("--execute-live", action="store_true", help="Execute one approved low-risk notify request.")
    parser.add_argument("--confirm-request-id", help="Required for --execute-live; must match --request-id.")
    parser.add_argument("--force", action="store_true", help="Re-run an approved request even if it has an executed marker.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.execute_live:
        if args.all or not args.request_id:
            print("status: blocked\nerror: --execute-live requires exactly one --request-id")
            return 1
        if args.confirm_request_id != args.request_id:
            print("status: blocked\nerror: confirm-request-id must match request-id")
            return 1
    try:
        files = selected_files(args)
    except Exception as exc:
        print(f"status: blocked\nerror: {exc}")
        return 1

    if not files:
        print("status: ok\nmessage: no approved requests")
        return 0

    records = [
        run_file(
            path,
            execute_live=args.execute_live,
            confirm_request_id=args.confirm_request_id,
            force=args.force,
        )
        for path in files
    ]
    for record in records:
        append_jsonl(args.log_path, record)
        print_record(record)

    return 0 if all(record["status"] == "ok" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
