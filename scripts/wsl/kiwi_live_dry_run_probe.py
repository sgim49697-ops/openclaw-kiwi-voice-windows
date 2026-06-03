# kiwi_live_dry_run_probe.py - verify Kiwi live transcript dry-run bridge.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = ROOT / ".debugloop" / "runs" / "kiwi-live-dry-run.jsonl"
DEFAULT_KIWI_PATH = r"C:\Users\ksg63\projects\kiwi-voice"
DEFAULT_SHIM_PATH = DEFAULT_KIWI_PATH + r"\dry-run-openclaw.cmd"
DEFAULT_ENV_PATH = Path("/mnt/c/Users/ksg63/projects/kiwi-voice/.env")

CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "notify",
        "message": "오픈클로, 테스트 알림 보내줘",
        "decision": "request_approval",
        "lane": "windows_wrapper",
        "riskTier": "low",
        "action": "notify",
        "approvalRequest": True,
    },
    {
        "id": "codex_plan",
        "message": "오픈클로, Codex로 현재 프로젝트 다음 계획 세워줘",
        "decision": "request_approval",
        "lane": "codex_plan",
        "riskTier": "medium",
        "action": "open_vscode_codex_plan",
        "approvalRequest": True,
    },
    {
        "id": "cancel",
        "message": "취소",
        "decision": "cancel",
        "lane": "none",
        "riskTier": "low",
        "action": None,
        "approvalRequest": False,
    },
    {
        "id": "critical",
        "message": "오픈클로, 결제하고 Gmail로 비밀번호 보내",
        "decision": "deny",
        "lane": "none",
        "riskTier": "critical",
        "action": None,
        "approvalRequest": False,
        "allowBlocked": True,
    },
)


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run_powershell(script: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    prefix = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$machinePath=[Environment]::GetEnvironmentVariable('Path','Machine'); "
        "$userPath=[Environment]::GetEnvironmentVariable('Path','User'); "
        "$env:Path=($machinePath,$userPath,$env:Path -join ';'); "
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", prefix + script],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def invoke_shim(shim_path: str, args: Sequence[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    quoted_args = " ".join(ps_literal(item) for item in args)
    return run_powershell(
        f"$shim={ps_literal(shim_path)}; Set-Location -LiteralPath (Split-Path -Parent $shim); & $shim {quoted_args}",
        timeout=timeout,
    )


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def read_log_entries() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"status": "invalid-json", "raw": line})
    return entries


def validate_preview(case: dict[str, Any], preview: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    route = preview.get("route", {})
    planner = preview.get("planner") if isinstance(preview.get("planner"), dict) else {}
    if preview.get("wouldExecute") is not False:
        errors.append("wouldExecute must remain false")

    if case.get("allowBlocked") and bool(route.get("blocked")) and not preview.get("approvalRequest"):
        return errors

    decision = route.get("decision") or planner.get("decision")
    if decision != case.get("decision"):
        errors.append(f"decision expected {case.get('decision')!r}, got {decision!r}")
    for key in ("lane", "riskTier", "action"):
        if route.get(key) != case.get(key):
            errors.append(f"{key} expected {case.get(key)!r}, got {route.get(key)!r}")
    if bool(preview.get("approvalRequest")) != bool(case["approvalRequest"]):
        errors.append(f"approvalRequest presence expected {case['approvalRequest']!r}")
    request = preview.get("approvalRequest")
    if request:
        payload_hash = request.get("payloadHash")
        if not (isinstance(payload_hash, str) and payload_hash.startswith("sha256:") and len(payload_hash) == 71):
            errors.append("approvalRequest payloadHash must be sha256 hex")
        if request.get("status") != "pending":
            errors.append("approvalRequest status must be pending")
    return errors


def find_new_log_entry(start_index: int, message: str) -> dict[str, Any] | None:
    for entry in read_log_entries()[start_index:]:
        if entry.get("status") == "dry-run" and entry.get("message") == message:
            return entry
    return None


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Kiwi live dry-run OpenClaw shim without executing actions.")
    parser.add_argument("--shim-path", default=DEFAULT_SHIM_PATH)
    parser.add_argument("--env-path", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--skip-env-check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    env_path = Path(args.env_path)
    report: dict[str, Any] = {
        "status": "passed",
        "shimPath": args.shim_path,
        "envPath": str(env_path),
        "checks": [],
        "cases": [],
    }
    failed = False

    if not args.skip_env_check:
        env_values = read_env(env_path)
        env_errors: list[str] = []
        if env_values.get("OPENCLAW_BIN") != args.shim_path:
            env_errors.append("OPENCLAW_BIN does not point to the dry-run shim")
        if env_values.get("KIWI_WS_ENABLED", "").lower() != "false":
            env_errors.append("KIWI_WS_ENABLED must remain false for dry-run planner probes")
        report["checks"].append({"id": "env", "status": "failed" if env_errors else "passed", "errors": env_errors})
        failed = failed or bool(env_errors)

    version = invoke_shim(args.shim_path, ["--version"])
    version_errors = [] if version.returncode == 0 else [version.stderr.strip() or version.stdout.strip()]
    report["checks"].append(
        {
            "id": "version",
            "status": "failed" if version_errors else "passed",
            "stdout": version.stdout.strip(),
            "errors": version_errors,
        }
    )
    failed = failed or bool(version_errors)

    denied = invoke_shim(args.shim_path, ["nodes", "status"])
    denied_errors = [] if denied.returncode != 0 else ["unsupported command unexpectedly succeeded"]
    report["checks"].append(
        {
            "id": "unsupported_command_denied",
            "status": "failed" if denied_errors else "passed",
            "returncode": denied.returncode,
            "stderr": denied.stderr.strip(),
            "errors": denied_errors,
        }
    )
    failed = failed or bool(denied_errors)

    start_index = len(read_log_entries())
    for case in CASES:
        completed = invoke_shim(
            args.shim_path,
            ["agent", "--session-id", "kiwi-voice", "--message", case["message"], "--timeout", "120"],
            timeout=240,
        )
        errors: list[str] = []
        if completed.returncode != 0:
            errors.append(completed.stderr.strip() or completed.stdout.strip() or f"shim exited {completed.returncode}")
        entry = find_new_log_entry(start_index, case["message"])
        if not entry:
            errors.append("dry-run log entry was not appended")
            preview = None
        else:
            preview = entry.get("result")
            if not isinstance(preview, dict):
                errors.append("dry-run log result is missing")
            else:
                errors.extend(validate_preview(case, preview))
        report["cases"].append(
            {
                "id": case["id"],
                "message": case["message"],
                "status": "failed" if errors else "passed",
                "stdout": completed.stdout.strip(),
                "errors": errors,
                "preview": preview,
            }
        )
        failed = failed or bool(errors)
        start_index = len(read_log_entries())

    if failed:
        report["status"] = "failed"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
