# debug_forever.py - supervised infinite debug cycle runner with stop-file control.
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".debugloop" / "runs"
STOP_PATH = ROOT / ".debugloop" / "STOP"
LOCK_PATH = RUNS_DIR / "forever.lock"
DEFAULT_LOG_PATH = RUNS_DIR / "forever.jsonl"
DEFAULT_SUMMARY_PATH = RUNS_DIR / "latest-forever-summary.md"
LATEST_AGENT_SUMMARY = RUNS_DIR / "latest-agent-summary.md"
DEFAULT_INTERVAL_SECONDS = 60.0
SAFETY_MARKERS = {
    "approval:status: manual_required",
    "intent:deny_delete: manual_required",
}


class LockHandle:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(data.get("pid", 0))
            except (OSError, ValueError, json.JSONDecodeError):
                pid = 0
            if pid > 0 and process_alive(pid):
                raise RuntimeError(f"debug forever loop already running with pid {pid}")
            self.path.unlink(missing_ok=True)

        payload = {
            "pid": os.getpid(),
            "createdAt": now_iso(),
            "cwd": str(ROOT),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(self.path, flags, 0o644)
        try:
            os.write(fd, (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        self.owned = True

    def release(self) -> None:
        if not self.owned:
            return
        try:
            self.path.unlink(missing_ok=True)
        finally:
            self.owned = False


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def short_output(output: str, limit: int = 1600) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def run_git_status() -> dict:
    completed = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return {
        "status": "dirty" if len(lines) > 1 else "clean",
        "detail": lines,
        "returncode": completed.returncode,
    }


def approval_counts() -> dict[str, int]:
    root = ROOT / ".debugloop" / "queue"
    counts: dict[str, int] = {}
    for status in ("pending", "approved", "rejected"):
        directory = root / status
        counts[status] = len(list(directory.glob("*.json"))) if directory.exists() else 0
    return counts


def read_latest_agent_summary() -> str:
    try:
        return LATEST_AGENT_SUMMARY.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_agent_status(summary: str) -> str | None:
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("- status:"):
            return stripped.split(":", 1)[1].strip()
    return None


def safety_blocked(summary: str) -> bool:
    return any(marker in summary for marker in SAFETY_MARKERS)


def agent_command(args: argparse.Namespace, *, dry_run: bool, monitor_only: bool, cycle: int) -> list[str]:
    if monitor_only:
        return ["python3", "scripts/wsl/debug_monitor.py", "--once"]

    command = [
        "python3",
        "scripts/wsl/debug_agent.py",
        "--once",
        "--probe-every",
        str(args.probe_every),
        "--cdp-recovery-max-failures",
        str(args.cdp_recovery_max_failures),
    ]
    if dry_run:
        command.append("--dry-run")
    if args.include_slow:
        command.append("--include-slow")
    if args.no_cdp_recovery:
        command.append("--no-cdp-recovery")
    if args.no_commit:
        command.append("--no-commit")
    return command


def terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()
        process.wait(timeout=10)


def run_child(command: Sequence[str], timeout: int) -> tuple[int, str, float]:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=command_env(),
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode or 0, (output or "").strip(), time.monotonic() - started
    except KeyboardInterrupt:
        terminate_process(process)
        raise
    except subprocess.TimeoutExpired:
        terminate_process(process)
        output = ""
        if process.stdout is not None:
            try:
                output = process.stdout.read() or ""
            except OSError:
                output = ""
        return 124, (output.strip() or "cycle command timed out"), time.monotonic() - started


def build_cycle_record(args: argparse.Namespace, cycle: int, monitor_only: bool) -> tuple[dict, bool]:
    worktree = run_git_status()
    dry_run = worktree["status"] != "clean"
    command = agent_command(args, dry_run=dry_run, monitor_only=monitor_only, cycle=cycle)
    returncode, output, duration = run_child(command, timeout=args.cycle_timeout)
    summary = read_latest_agent_summary() if not monitor_only else output
    agent_status = parse_agent_status(summary)
    safety = safety_blocked(summary)

    status = "ok"
    if returncode == 124:
        status = "timeout"
    elif safety:
        status = "safety_blocked"
    elif agent_status:
        status = agent_status
    elif returncode != 0:
        status = "blocked"

    record = {
        "timestamp": now_iso(),
        "mode": "debug_forever",
        "cycle": cycle,
        "status": status,
        "returncode": returncode,
        "durationSeconds": round(duration, 3),
        "dryRun": dry_run,
        "monitorOnly": monitor_only,
        "worktree": worktree,
        "approvals": approval_counts(),
        "command": command,
        "detail": short_output(output),
        "stopFilePresent": STOP_PATH.exists(),
        "safetyBlocked": safety,
    }
    return record, safety


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(path: Path, record: dict, *, stopped: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Debug Forever Summary",
        "",
        f"- timestamp: {record.get('timestamp', now_iso())}",
        f"- cycle: {record.get('cycle', 0)}",
        f"- status: {record.get('status', 'unknown')}",
        f"- dryRun: {record.get('dryRun', False)}",
        f"- monitorOnly: {record.get('monitorOnly', False)}",
        f"- worktree: {record.get('worktree', {}).get('status', 'unknown')}",
        f"- approvals: {record.get('approvals', {})}",
        f"- stopped: {stopped}",
        "",
        "## Command",
        "```text",
        " ".join(str(part) for part in record.get("command", [])),
        "```",
    ]
    detail = record.get("detail")
    if detail:
        lines.extend(["", "## Detail", detail])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"cycle: {record['cycle']}")
    print(f"dryRun: {record['dryRun']}")
    print(f"monitorOnly: {record['monitorOnly']}")
    print(f"worktree: {record['worktree']['status']}")
    print(f"approvals: {record['approvals']}")
    if record.get("detail"):
        print(f"detail: {record['detail']}")


def create_stop_file() -> None:
    STOP_PATH.parent.mkdir(parents=True, exist_ok=True)
    STOP_PATH.write_text(json.dumps({"createdAt": now_iso()}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def clear_stop_file() -> None:
    STOP_PATH.unlink(missing_ok=True)


def print_status(summary_path: Path) -> int:
    if not summary_path.exists():
        print("No debug forever summary exists.")
        return 1
    print(summary_path.read_text(encoding="utf-8").rstrip())
    return 0


def run_loop(args: argparse.Namespace) -> int:
    lock = LockHandle(args.lock_path)
    monitor_only = False
    last_record: dict | None = None
    lock.acquire()
    try:
        cycle = 0
        while True:
            if STOP_PATH.exists():
                stop_record = last_record or {
                    "timestamp": now_iso(),
                    "cycle": cycle,
                    "status": "stopped",
                    "dryRun": False,
                    "monitorOnly": monitor_only,
                    "worktree": run_git_status(),
                    "approvals": approval_counts(),
                    "command": [],
                    "detail": "STOP file present before next cycle",
                }
                write_summary(args.summary_path, stop_record, stopped=True)
                print("status: stopped")
                print(f"stopFile: {STOP_PATH.relative_to(ROOT)}")
                return 0

            cycle += 1
            record, safety = build_cycle_record(args, cycle, monitor_only=monitor_only)
            append_jsonl(args.log_path, record)
            write_summary(args.summary_path, record)
            print_record(record)
            last_record = record
            monitor_only = monitor_only or safety

            if args.once or (args.iterations > 0 and cycle >= args.iterations):
                return 0 if record["status"] not in {"timeout", "safety_blocked"} else 1

            time.sleep(max(args.interval, 0))
    except KeyboardInterrupt:
        if last_record is None:
            last_record = {
                "timestamp": now_iso(),
                "cycle": 0,
                "status": "interrupted",
                "dryRun": False,
                "monitorOnly": monitor_only,
                "worktree": run_git_status(),
                "approvals": approval_counts(),
                "command": [],
                "detail": "interrupted before first cycle completed",
            }
        last_record["status"] = "interrupted"
        write_summary(args.summary_path, last_record, stopped=True)
        print("status: interrupted")
        return 130
    finally:
        lock.release()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run debug agent cycles until the user stops them.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one supervisor cycle.")
    mode.add_argument("--watch", action="store_true", help="Run until STOP file, interrupt, or --iterations limit.")
    mode.add_argument("--stop", action="store_true", help="Create .debugloop/STOP.")
    mode.add_argument("--clear-stop", action="store_true", help="Remove .debugloop/STOP.")
    mode.add_argument("--status", action="store_true", help="Print the latest forever summary.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--iterations", type=int, default=0, help="0 means forever when --watch is set.")
    parser.add_argument("--cycle-timeout", type=int, default=900)
    parser.add_argument("--include-slow", action="store_true")
    parser.add_argument("--probe-every", type=int, default=3)
    parser.add_argument("--no-cdp-recovery", action="store_true")
    parser.add_argument("--cdp-recovery-max-failures", type=int, default=3)
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("--lock-path", type=Path, default=LOCK_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.stop:
        create_stop_file()
        print(f"created: {STOP_PATH.relative_to(ROOT)}")
        return 0
    if args.clear_stop:
        clear_stop_file()
        print(f"removed: {STOP_PATH.relative_to(ROOT)}")
        return 0
    if args.status:
        return print_status(args.summary_path)
    if not args.once and not args.watch:
        args.once = True
    if args.interval < 0:
        print("--interval must be non-negative", file=sys.stderr)
        return 2
    if args.iterations < 0:
        print("--iterations must be non-negative", file=sys.stderr)
        return 2
    if args.cycle_timeout <= 0:
        print("--cycle-timeout must be positive", file=sys.stderr)
        return 2
    return run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
