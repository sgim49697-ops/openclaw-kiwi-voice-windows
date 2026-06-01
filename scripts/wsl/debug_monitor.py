# debug_monitor.py - read-only OpenClaw voice wrapper status monitor.
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / ".debugloop" / "runs" / "latest.jsonl"


@dataclass
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    output: str


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_command(name: str, command: Sequence[str], timeout: int = 15) -> CommandResult:
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
      return CommandResult(name, list(command), completed.returncode, output)
    except FileNotFoundError as exc:
      return CommandResult(name, list(command), 127, str(exc))
    except subprocess.TimeoutExpired as exc:
      output = (exc.stdout or "")
      if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
      return CommandResult(name, list(command), 124, output.strip() or "command timed out")


def short_output(output: str, limit: int = 500) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
      return collapsed
    return collapsed[: limit - 3] + "..."


def check_repo() -> dict:
    result = run_command("repo", ["git", "status", "--short", "--branch"])
    lines = [line for line in result.output.splitlines() if line.strip()]
    dirty = len(lines) > 1
    status = "warning" if dirty else "ok"
    return {
        "name": "repo",
        "status": status if result.returncode == 0 else "blocked",
        "summary": "worktree dirty" if dirty else "worktree clean",
        "returncode": result.returncode,
        "detail": short_output(result.output),
    }


def check_gateway() -> dict:
    result = run_command("gateway", ["openclaw", "gateway", "status"], timeout=20)
    ok = result.returncode == 0 and "Connectivity probe: ok" in result.output
    return {
        "name": "gateway",
        "status": "ok" if ok else "blocked",
        "summary": "gateway reachable" if ok else "gateway unreachable or unhealthy",
        "returncode": result.returncode,
        "detail": short_output(result.output),
    }


def check_approvals() -> dict:
    result = run_command("approvals", ["openclaw", "approvals", "get", "--gateway"], timeout=20)
    required = [
        "security=allowlist",
        "ask=always",
        "askFallback=deny",
        "autoAllowSkills=off",
    ]
    missing = [item for item in required if item not in result.output]
    ok = result.returncode == 0 and not missing
    return {
        "name": "approvals",
        "status": "ok" if ok else "blocked",
        "summary": "exec approvals locked" if ok else f"exec approvals missing: {', '.join(missing)}",
        "returncode": result.returncode,
        "detail": short_output(result.output),
    }


def check_nodes() -> dict:
    status_result = run_command("nodes_status", ["openclaw", "nodes", "status"], timeout=20)
    pending_result = run_command("nodes_pending", ["openclaw", "nodes", "pending", "--json"], timeout=20)
    output = f"{status_result.output}\n{pending_result.output}".strip()
    blocked = "Known: 0" in status_result.output or "Connected: 0" in status_result.output
    status = "blocked" if blocked or status_result.returncode != 0 else "ok"
    return {
        "name": "nodes",
        "status": status,
        "summary": "no connected Windows Node" if status == "blocked" else "node connected",
        "returncode": status_result.returncode,
        "detail": short_output(output),
    }


def check_browser() -> dict:
    result = run_command("browser", ["openclaw", "browser", "status"], timeout=20)
    unknown_method = "unknown method: browser.request" in result.output
    ok = result.returncode == 0 and not unknown_method
    return {
        "name": "browser",
        "status": "ok" if ok else "blocked",
        "summary": "browser status ok" if ok else "browser capability unavailable",
        "returncode": result.returncode,
        "detail": short_output(result.output),
    }


def summarize(checks: list[dict]) -> str:
    if any(check["status"] == "blocked" for check in checks):
      return "blocked"
    if any(check["status"] == "warning" for check in checks):
      return "warning"
    return "ok"


def build_record() -> dict:
    checks = [
        check_repo(),
        check_gateway(),
        check_approvals(),
        check_nodes(),
        check_browser(),
    ]
    return {
        "timestamp": now_iso(),
        "status": summarize(checks),
        "mode": "read_only_monitor",
        "checks": checks,
    }


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    for check in record["checks"]:
      print(f"- {check['name']}: {check['status']} - {check['summary']}")
      if check["status"] != "ok" and check.get("detail"):
        print(f"  detail: {check['detail']}")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
      handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only debug monitor for OpenClaw voice wrapper work.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one check and print the summary.")
    mode.add_argument("--watch", action="store_true", help="Run repeated checks.")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between watch checks.")
    parser.add_argument("--iterations", type=int, default=0, help="Watch iterations; 0 means forever.")
    parser.add_argument("--write-log", action="store_true", help="Append JSONL output to the debug loop log.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="JSONL log path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    watch = args.watch
    iterations = args.iterations if watch else 1
    count = 0

    while True:
      record = build_record()
      print_record(record)
      if args.write_log or watch:
        append_jsonl(args.log_path, record)

      count += 1
      if not watch or (iterations > 0 and count >= iterations):
        return 0
      time.sleep(max(args.interval, 0))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
