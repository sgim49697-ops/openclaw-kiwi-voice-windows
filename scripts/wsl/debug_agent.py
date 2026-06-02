# debug_agent.py - supervised L2 repair runner for the debug loop.
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import debug_autoloop as autoloop


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".debugloop" / "runs"
DEFAULT_LOG_PATH = RUNS_DIR / "agent.jsonl"
DEFAULT_SUMMARY_PATH = RUNS_DIR / "latest-agent-summary.md"
REPAIR_PREFIX = "debug-agent:"
REQUIRED_REPAIR_FLAGS = {"--repair", "--confirm-safe-l2"}
FORBIDDEN_REPAIR_FLAGS = {"--commit", "--push", "--approve", "--deploy", "--exec", "--shell"}
DEFAULT_COMMIT_MESSAGE = "\uc790\ub3d9 \ub514\ubc84\uadf8 \ub8e8\ud504 L2 \uc218\uc815"
L2_PATH_PREFIXES = ("docs/", "policies/", "schemas/", "tests/", "evals/", "scripts/wsl/")


@dataclass(frozen=True)
class RepairSpec:
    name: str
    path: str
    line: int
    command: list[str]
    timeout: int = 120


@dataclass(frozen=True)
class RepairResult:
    spec: RepairSpec
    status: str
    returncode: int
    output: str
    durationSeconds: float
    changedFiles: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def command_env() -> dict[str, str]:
    return autoloop.command_env()


def short_output(output: str, limit: int = 700) -> str:
    return autoloop.short_output(output, limit=limit)


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def porcelain_entries() -> list[str]:
    result = run_git(["status", "--porcelain=v1", "--untracked-files=normal"])
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def parse_porcelain_path(entry: str) -> list[str]:
    status = entry[:2]
    path_text = entry[3:]
    if " -> " in path_text:
        old_path, new_path = path_text.split(" -> ", 1)
        return [old_path.strip(), new_path.strip()]
    if status == "??":
        return [path_text.strip()]
    return [path_text.strip()]


def tracked_dirty_files() -> set[str]:
    files: set[str] = set()
    for entry in porcelain_entries():
        if entry.startswith("?? "):
            continue
        files.update(parse_porcelain_path(entry))
    return files


def changed_files() -> list[str]:
    files: set[str] = set()
    for entry in porcelain_entries():
        files.update(parse_porcelain_path(entry))
    return sorted(files)


def is_allowed_l2_path(path_text: str) -> bool:
    path = (ROOT / path_text).resolve()
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        return False
    return any(rel.startswith(prefix) for prefix in L2_PATH_PREFIXES)


def is_repo_python_path(path_text: str) -> bool:
    return autoloop.is_repo_python_path(path_text)


def is_safe_repair_command(command: Sequence[str], marker_path: Path) -> bool:
    if len(command) < 2:
        return False
    if command[0] not in {"python", "python3"}:
        return False
    if command[1] in {"-c", "-m"}:
        return False
    if not is_repo_python_path(command[1]):
        return False
    command_path = (ROOT / command[1]).resolve()
    if command_path != marker_path.resolve():
        return False
    flags = {item.split("=", 1)[0] for item in command[2:] if item.startswith("--")}
    if not REQUIRED_REPAIR_FLAGS.issubset(flags):
        return False
    return not any(flag in FORBIDDEN_REPAIR_FLAGS for flag in flags)


def parse_repair_markers(path: Path) -> tuple[list[RepairSpec], list[dict]]:
    specs: list[RepairSpec] = []
    ignored: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return specs, ignored
    except OSError as exc:
        ignored.append({"path": relative(path), "reason": str(exc)})
        return specs, ignored

    for line_number, line in enumerate(lines[:30], start=1):
        stripped = line.strip()
        if not stripped.startswith("#") or REPAIR_PREFIX not in stripped:
            continue
        marker = stripped.split(REPAIR_PREFIX, 1)[1].strip()
        if not marker.startswith("repair="):
            ignored.append({"path": relative(path), "line": line_number, "reason": "missing repair="})
            continue
        raw_command = marker.removeprefix("repair=").strip()
        try:
            command = shlex.split(raw_command)
        except ValueError as exc:
            ignored.append({"path": relative(path), "line": line_number, "reason": str(exc)})
            continue
        if not is_safe_repair_command(command, path):
            ignored.append({"path": relative(path), "line": line_number, "reason": "unsafe repair command"})
            continue
        specs.append(
            RepairSpec(
                name=f"repair:{relative(path)}:{line_number}",
                path=relative(path),
                line=line_number,
                command=command,
            )
        )
    return specs, ignored


def discover_repair_markers(files: Sequence[Path]) -> tuple[list[RepairSpec], list[dict]]:
    specs: list[RepairSpec] = []
    ignored: list[dict] = []
    for path in files:
        if path.suffix != ".py":
            continue
        file_specs, file_ignored = parse_repair_markers(path)
        specs.extend(file_specs)
        ignored.extend(file_ignored)
    return specs, ignored


def failed_marker_paths(autoloop_record: dict) -> set[str]:
    paths: set[str] = set()
    for result in autoloop_record.get("results", []):
        if result.get("status") == "ok":
            continue
        name = result.get("name", "")
        if not name.startswith("marker:"):
            continue
        marker = name.removeprefix("marker:")
        path_text, _, _line = marker.rpartition(":")
        if path_text:
            paths.add(path_text)
    return paths


def classify_blocked_result(result: dict, repairable_paths: set[str]) -> str:
    if result.get("status") == "ok":
        return "ok"
    name = result.get("name", "")
    if name.startswith("marker:"):
        marker = name.removeprefix("marker:")
        path_text, _, _line = marker.rpartition(":")
        if path_text in repairable_paths:
            return "repairable_l2"
        return "manual_required"
    if name in {"debug:monitor", "browser:probe", "browser:probe:on_blocked", "browser:cdp-recovery"}:
        return "external_blocked"
    if name == "kiwi:live-dry-run":
        return "manual_required"
    if name in {"approval:status"} and result.get("status") == "warning":
        return "manual_required"
    if name.startswith("intent:") and result.get("status") != "ok":
        return "manual_required"
    return "manual_required"


def run_command(command: Sequence[str], timeout: int) -> tuple[int, str, float]:
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
    return returncode, output, time.monotonic() - started


def run_repair(spec: RepairSpec, dry_run: bool) -> RepairResult:
    before = set(changed_files())
    if dry_run:
        return RepairResult(spec, "dry_run", 0, "repair skipped by --dry-run", 0.0, sorted(before))
    returncode, output, duration = run_command(spec.command, spec.timeout)
    after = set(changed_files())
    changed = sorted(after - before if after - before else after)
    status = "ok" if returncode == 0 else "blocked"
    return RepairResult(spec, status, returncode, output, duration, changed)


def command_result_record(result: RepairResult) -> dict:
    return {
        "name": result.spec.name,
        "path": result.spec.path,
        "line": result.spec.line,
        "status": result.status,
        "returncode": result.returncode,
        "durationSeconds": round(result.durationSeconds, 3),
        "command": result.spec.command,
        "changedFiles": result.changedFiles,
        "detail": short_output(result.output),
    }


def verification_specs() -> list[autoloop.CommandSpec]:
    files = autoloop.tracked_and_untracked_files()
    specs = [
        spec
        for spec in autoloop.base_specs(files, include_browser_probe=False)
        if spec.name in {"python:compile:wsl", "policy:validate", "schema:validate"}
    ]
    specs.append(autoloop.CommandSpec(name="git:diff-check", command=["git", "diff", "--check"], timeout=30))
    return specs


def run_verification() -> list[dict]:
    results = [autoloop.run_command(spec) for spec in verification_specs()]
    return [autoloop.result_record(result) for result in results]


def verification_ok(results: Sequence[dict]) -> bool:
    return all(result.get("status") == "ok" and result.get("returncode") == 0 for result in results)


def commit_changes(paths: Sequence[str], message: str) -> dict:
    allowed = [path for path in paths if is_allowed_l2_path(path)]
    if not allowed:
        return {"status": "skipped", "reason": "no allowed L2 changes"}
    add_result = run_git(["add", "--", *allowed])
    if add_result.returncode != 0:
        return {"status": "blocked", "reason": short_output(add_result.stdout)}
    commit_result = run_git(["commit", "-m", message])
    if commit_result.returncode != 0:
        return {"status": "blocked", "reason": short_output(commit_result.stdout)}
    return {"status": "ok", "detail": short_output(commit_result.stdout)}


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Debug Agent Summary",
        "",
        f"- timestamp: {record['timestamp']}",
        f"- cycle: {record['cycle']}",
        f"- status: {record['status']}",
        f"- dryRun: {record['dryRun']}",
        f"- startClean: {record['startClean']}",
        f"- repairCandidates: {len(record['repairCandidates'])}",
        "",
        "## Classifications",
    ]
    for item in record["classifications"]:
        lines.append(f"- {item['name']}: {item['classification']}")
    lines.append("")
    lines.append("## Repairs")
    if record["repairs"]:
        for repair in record["repairs"]:
            lines.append(f"- {repair['name']}: {repair['status']} ({repair['returncode']})")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"cycle: {record['cycle']}")
    print(f"dryRun: {record['dryRun']}")
    print(f"startClean: {record['startClean']}")
    print(f"repairCandidates: {len(record['repairCandidates'])}")
    for item in record["classifications"]:
        if item["classification"] != "ok":
            print(f"- {item['name']}: {item['classification']}")
    for repair in record["repairs"]:
        print(f"- {repair['name']}: {repair['status']} ({repair['returncode']})")
        if repair["detail"]:
            print(f"  detail: {repair['detail']}")
    if record["commit"]["status"] != "skipped":
        print(f"commit: {record['commit']['status']} - {record['commit'].get('detail') or record['commit'].get('reason')}")


def summarize_agent_status(
    classifications: Sequence[dict],
    repairs: Sequence[dict],
    verification: Sequence[dict],
    unsafe_changes: Sequence[str],
    commit: dict,
) -> str:
    if unsafe_changes:
        return "manual_required"
    if any(repair["status"] == "blocked" for repair in repairs):
        return "manual_required"
    if verification and not verification_ok(verification):
        return "manual_required"
    if commit.get("status") == "blocked":
        return "manual_required"
    if any(item["classification"] == "external_blocked" for item in classifications):
        return "external_blocked"
    if any(item["classification"] == "manual_required" for item in classifications):
        return "manual_required"
    if any(item["classification"] == "repairable_l2" for item in classifications):
        if repairs:
            return "repaired" if all(repair["status"] in {"ok", "dry_run"} for repair in repairs) else "manual_required"
        return "repairable_l2"
    return "ok"


def run_agent_cycle(args: argparse.Namespace, cycle_index: int) -> dict:
    protected = tracked_dirty_files()
    start_clean = not protected and not changed_files()
    autoloop_args = argparse.Namespace(
        include_slow=args.include_slow,
        probe_every=args.probe_every,
        no_cdp_recovery=args.no_cdp_recovery,
        cdp_recovery_max_failures=args.cdp_recovery_max_failures,
    )
    autoloop_record = autoloop.run_cycle(autoloop_args, cycle_index)
    files = autoloop.tracked_and_untracked_files()
    repair_specs, ignored_repairs = discover_repair_markers(files)
    repairs_by_path = {spec.path: spec for spec in repair_specs}
    failed_paths = failed_marker_paths(autoloop_record)
    repair_candidates = [repairs_by_path[path] for path in sorted(failed_paths) if path in repairs_by_path]
    repairable_paths = {spec.path for spec in repair_candidates}
    classifications = [
        {
            "name": result["name"],
            "status": result["status"],
            "classification": classify_blocked_result(result, repairable_paths),
        }
        for result in autoloop_record.get("results", [])
    ]

    repairs: list[dict] = []
    verification: list[dict] = []
    commit = {"status": "skipped", "reason": "no repair changes"}
    unsafe_changes: list[str] = []

    if repair_candidates and protected:
        repairs = [
            {
                "name": spec.name,
                "path": spec.path,
                "line": spec.line,
                "status": "skipped",
                "returncode": 0,
                "durationSeconds": 0.0,
                "command": spec.command,
                "changedFiles": [],
                "detail": f"tracked dirty files protected: {', '.join(sorted(protected))}",
            }
            for spec in repair_candidates
        ]
    else:
        for spec in repair_candidates:
            repairs.append(command_result_record(run_repair(spec, dry_run=args.dry_run)))

    if repairs and all(repair["status"] in {"ok", "dry_run"} for repair in repairs):
        current_changes = changed_files()
        unsafe_changes = [path for path in current_changes if not is_allowed_l2_path(path)]
        if current_changes and not args.dry_run and not unsafe_changes:
            verification = run_verification()
            if start_clean and verification_ok(verification) and not args.no_commit:
                commit = commit_changes(current_changes, args.commit_message)
            elif args.no_commit:
                commit = {"status": "skipped", "reason": "--no-commit set"}
            elif not start_clean:
                commit = {"status": "skipped", "reason": "cycle did not start clean"}
            elif not verification_ok(verification):
                commit = {"status": "skipped", "reason": "verification failed"}
        elif args.dry_run:
            commit = {"status": "skipped", "reason": "--dry-run set"}
        elif unsafe_changes:
            commit = {"status": "skipped", "reason": "unsafe changes detected"}

    record = {
        "timestamp": now_iso(),
        "cycle": cycle_index,
        "mode": "safe_l2_agent",
        "dryRun": args.dry_run,
        "startClean": start_clean,
        "protectedTrackedFiles": sorted(protected),
        "autoloop": autoloop_record,
        "repairMarkers": {
            "count": len(repair_specs),
            "ignored": ignored_repairs,
        },
        "repairCandidates": [spec.__dict__ for spec in repair_candidates],
        "classifications": classifications,
        "repairs": repairs,
        "unsafeChanges": unsafe_changes,
        "verification": verification,
        "commit": commit,
    }
    record["status"] = summarize_agent_status(classifications, repairs, verification, unsafe_changes, commit)
    return record


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervised L2 repair agent for OpenClaw voice debug loops.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one agent cycle.")
    mode.add_argument("--watch", action="store_true", help="Run repeated agent cycles.")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between watch cycles.")
    parser.add_argument("--iterations", type=int, default=0, help="Watch iterations; 0 means forever.")
    parser.add_argument("--cycle-index", type=int, help="External supervisor cycle number for --once mode.")
    parser.add_argument("--dry-run", action="store_true", help="Classify and print candidate repairs without running them.")
    parser.add_argument("--include-slow", action="store_true", help="Always include slow autoloop probes.")
    parser.add_argument("--probe-every", type=int, default=3, help="Run browser probe every N blocked cycles.")
    parser.add_argument("--no-cdp-recovery", action="store_true", help="Do not auto-restart the isolated browser on page-level CDP stalls.")
    parser.add_argument("--cdp-recovery-max-failures", type=int, default=3, help="Stop CDP auto-restart after N consecutive failed recoveries.")
    parser.add_argument("--no-commit", action="store_true", help="Do not commit verified L2 repair changes.")
    parser.add_argument("--commit-message", default=DEFAULT_COMMIT_MESSAGE, help="Git commit message for verified L2 repairs.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="JSONL agent log path.")
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH, help="Latest agent summary path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.cycle_index is not None and args.cycle_index <= 0:
        print("--cycle-index must be positive", file=sys.stderr)
        return 2
    if args.cycle_index is not None and args.watch:
        print("--cycle-index is only supported with --once", file=sys.stderr)
        return 2

    watch = args.watch
    iterations = args.iterations if watch else 1
    count = 0
    last_record: dict | None = None

    while True:
        count += 1
        cycle_index = args.cycle_index if args.cycle_index is not None else count
        record = run_agent_cycle(args, cycle_index)
        append_jsonl(args.log_path, record)
        write_summary(args.summary_path, record)
        print_record(record)
        last_record = record

        if not watch or (iterations > 0 and count >= iterations):
            break
        time.sleep(max(args.interval, 0))

    ok_statuses = {"ok", "repaired"}
    return 0 if last_record and last_record["status"] in ok_statuses else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
