# browser_cdp_recovery.py - recover isolated OpenClaw browser CDP page-level stalls.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "browser"
RUNS_DIR = ROOT / ".debugloop" / "runs"
DEFAULT_STATE_PATH = RUNS_DIR / "browser-cdp-recovery-state.json"
DEFAULT_RECORD_PATH = ARTIFACT_DIR / "cdp-recovery.json"
DEFAULT_BEFORE_PATH = ARTIFACT_DIR / "cdp-recovery-before.json"
DEFAULT_AFTER_PATH = ARTIFACT_DIR / "cdp-recovery-after.json"
DEFAULT_PROFILE = "openclaw"
DEFAULT_URL = "https://example.com"
BASELINE_CHECKS = ("json_version", "json_list", "browser_get_version")
PAGE_LEVEL_FAILURES = {
    "runtime_evaluate_title",
    "target_attach_runtime_evaluate",
    "page_capture_screenshot",
    "target_attach_capture_screenshot",
    "accessibility_get_full_ax_tree",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def short_output(output: str, limit: int = 1200) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_command(name: str, command: Sequence[str], timeout: int) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env=command_env(),
        )
        output = completed.stdout.strip()
        returncode = completed.returncode
    except FileNotFoundError as exc:
        output = str(exc)
        returncode = 127
    except subprocess.TimeoutExpired as exc:
        raw_output = exc.stdout or ""
        if isinstance(raw_output, bytes):
            raw_output = raw_output.decode("utf-8", errors="replace")
        output = raw_output.strip() or "command timed out"
        returncode = 124

    return {
        "name": name,
        "command": list(command),
        "returncode": returncode,
        "status": "ok" if returncode == 0 else "blocked",
        "durationMs": int((time.monotonic() - started) * 1000),
        "detail": short_output(output),
    }


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def checks_by_name(record: dict) -> dict[str, dict]:
    return {str(check.get("name")): check for check in record.get("checks", []) if isinstance(check, dict)}


def is_recoverable_page_level_block(record: dict) -> tuple[bool, str]:
    checks = checks_by_name(record)
    baseline_ok = all(checks.get(name, {}).get("status") == "ok" for name in BASELINE_CHECKS)
    if not baseline_ok:
        return False, "baseline CDP checks are not all ok"
    if record.get("status") == "ok":
        return False, "CDP page-level checks are already ok"

    failed_at = record.get("failedAt")
    page_failures = [
        name
        for name, check in checks.items()
        if name in PAGE_LEVEL_FAILURES and check.get("status") != "ok"
    ]
    if failed_at in PAGE_LEVEL_FAILURES or page_failures:
        return True, f"page-level CDP checks blocked: {', '.join(page_failures) or failed_at}"
    return False, f"CDP failure is not page-level recoverable: {failed_at or '<unknown>'}"


def run_cdp_probe(out_path: Path, timeout_ms: int) -> tuple[dict, dict]:
    result = run_command(
        "cdp_probe",
        [
            "node",
            "scripts/wsl/cdp_probe.mjs",
            "--timeout-ms",
            str(timeout_ms),
            "--out",
            relative(out_path),
        ],
        timeout=max(45, int((timeout_ms * 8) / 1000) + 20),
    )
    return result, load_json(out_path)


def browser_command(profile: str, *args: str) -> list[str]:
    return ["openclaw", "browser", "--browser-profile", profile, *args]


def load_state(path: Path) -> dict:
    state = load_json(path)
    if not state:
        return {"consecutiveFailures": 0, "lastRestartCycle": None}
    state.setdefault("consecutiveFailures", 0)
    state.setdefault("lastRestartCycle", None)
    return state


def save_state(path: Path, state: dict) -> None:
    state["updatedAt"] = now_iso()
    write_json(path, state)


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"recovery: {record['recovery']}")
    if record.get("reason"):
        print(f"reason: {record['reason']}")
    print(f"profile: {record['profile']}")
    for step in record.get("steps", []):
        print(f"- {step['name']}: {step['status']} ({step['returncode']})")
        if step.get("detail"):
            print(f"  detail: {step['detail']}")
    for name, path in record.get("artifacts", {}).items():
        print(f"artifact[{name}]: {path}")


def run_recovery(args: argparse.Namespace) -> dict:
    if args.profile != DEFAULT_PROFILE:
        return {
            "timestamp": now_iso(),
            "mode": "browser_cdp_recovery",
            "status": "blocked",
            "recovery": "refused",
            "reason": f"profile must be {DEFAULT_PROFILE}; refusing {args.profile}",
            "profile": args.profile,
            "steps": [],
            "artifacts": {},
        }

    state = load_state(args.state_path)
    record = {
        "timestamp": now_iso(),
        "mode": "browser_cdp_recovery",
        "status": "blocked",
        "recovery": "pending",
        "reason": "",
        "profile": args.profile,
        "url": args.url,
        "cycle": args.cycle,
        "state": state,
        "steps": [],
        "artifacts": {},
    }

    before_result, before_record = run_cdp_probe(args.before_path, args.timeout_ms)
    record["steps"].append(before_result)
    record["artifacts"]["before"] = relative(args.before_path)

    if before_record.get("status") == "ok":
        state["consecutiveFailures"] = 0
        save_state(args.state_path, state)
        record.update({"status": "ok", "recovery": "not_needed", "reason": "CDP page-level checks are healthy"})
        record["state"] = state
        return record

    recoverable, reason = is_recoverable_page_level_block(before_record)
    if not recoverable:
        record.update({"status": "blocked", "recovery": "skipped", "reason": reason})
        return record

    if state.get("consecutiveFailures", 0) >= args.max_failures:
        record.update(
            {
                "status": "blocked",
                "recovery": "max_failures_reached",
                "reason": f"{reason}; consecutive restart failures reached {args.max_failures}",
            }
        )
        return record

    if args.cycle and state.get("lastRestartCycle") == args.cycle:
        record.update({"status": "blocked", "recovery": "already_restarted_this_cycle", "reason": reason})
        return record

    if args.no_restart:
        record.update({"status": "blocked", "recovery": "dry_run", "reason": reason})
        return record

    for name, command, timeout in [
        ("browser_stop", browser_command(args.profile, "stop"), 30),
        ("browser_start", browser_command(args.profile, "start"), 45),
        ("browser_open", browser_command(args.profile, "open", args.url), 45),
    ]:
        step = run_command(name, command, timeout=timeout)
        record["steps"].append(step)
        if step["returncode"] != 0 and name != "browser_stop":
            state["consecutiveFailures"] = int(state.get("consecutiveFailures", 0)) + 1
            state["lastRestartCycle"] = args.cycle
            save_state(args.state_path, state)
            record["state"] = state
            record.update({"status": "blocked", "recovery": "restart_failed", "reason": step["detail"]})
            return record

    after_result, after_record = run_cdp_probe(args.after_path, args.timeout_ms)
    record["steps"].append(after_result)
    record["artifacts"]["after"] = relative(args.after_path)
    state["lastRestartCycle"] = args.cycle

    if after_record.get("status") == "ok":
        state["consecutiveFailures"] = 0
        save_state(args.state_path, state)
        record["state"] = state
        record.update({"status": "ok", "recovery": "restarted", "reason": "CDP page-level checks recovered"})
        return record

    state["consecutiveFailures"] = int(state.get("consecutiveFailures", 0)) + 1
    save_state(args.state_path, state)
    record["state"] = state
    record.update({"status": "blocked", "recovery": "restart_did_not_recover", "reason": "CDP page-level checks remain blocked"})
    return record


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover isolated OpenClaw browser CDP page-level stalls.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"Browser profile to recover; must be {DEFAULT_PROFILE}.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Safe read-only URL to open after restart.")
    parser.add_argument("--timeout-ms", type=int, default=5000, help="Per-CDP-command timeout in milliseconds.")
    parser.add_argument("--cycle", type=int, help="Debug loop cycle number for one-restart-per-cycle protection.")
    parser.add_argument("--max-failures", type=int, default=3, help="Stop restarting after this many consecutive failed recoveries.")
    parser.add_argument("--no-restart", action="store_true", help="Classify recoverability without restarting the browser.")
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--record-path", type=Path, default=DEFAULT_RECORD_PATH)
    parser.add_argument("--before-path", type=Path, default=DEFAULT_BEFORE_PATH)
    parser.add_argument("--after-path", type=Path, default=DEFAULT_AFTER_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.timeout_ms <= 0:
        print("--timeout-ms must be positive", file=sys.stderr)
        return 2
    if args.max_failures <= 0:
        print("--max-failures must be positive", file=sys.stderr)
        return 2

    record = run_recovery(args)
    write_json(args.record_path, record)
    print_record(record)
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
