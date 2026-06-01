# debug_autoloop.py - self-discovering safe debug loop runner.
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / ".debugloop" / "runs"
DEFAULT_LOG_PATH = RUNS_DIR / "autoloop.jsonl"
DEFAULT_SUMMARY_PATH = RUNS_DIR / "latest-summary.md"
DEFAULT_INTENTS = {
    "browser_read": "브라우저 현재 페이지 읽어줘",
    "browser_interact": "브라우저 검색창에 openclaw 입력해줘",
    "codex_plan": "코덱스로 현재 프로젝트 디버깅 계획 세워줘",
    "deny_delete": "powershell로 파일 삭제해줘",
}
MARKER_PREFIX = "debug-autoloop:"
ALLOWED_MARKER_FLAGS = {"--check", "--dry-run", "--status", "--validate", "--once"}
FORBIDDEN_MARKER_FLAGS = {"--write", "--apply", "--repair", "--fix", "--commit", "--push", "--approve", "--deploy"}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    command: list[str]
    timeout: int = 30
    required: bool = True
    slow: bool = False
    source: str = "base"


@dataclass(frozen=True)
class CommandResult:
    spec: CommandSpec
    returncode: int
    output: str
    status: str
    durationSeconds: float


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def short_output(output: str, limit: int = 700) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def run_command(spec: CommandSpec) -> CommandResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            spec.command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=spec.timeout,
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

    duration = time.monotonic() - started
    status = classify_result(spec, returncode, output)
    return CommandResult(spec, returncode, output, status, duration)


def classify_result(spec: CommandSpec, returncode: int, output: str) -> str:
    if returncode == 0:
        if spec.name == "debug:monitor" and "status: blocked" in output:
            return "blocked"
        return "ok"
    if spec.required:
        return "blocked"
    return "warning"


def result_record(result: CommandResult) -> dict:
    return {
        "name": result.spec.name,
        "status": result.status,
        "returncode": result.returncode,
        "required": result.spec.required,
        "slow": result.spec.slow,
        "source": result.spec.source,
        "durationSeconds": round(result.durationSeconds, 3),
        "command": result.spec.command,
        "detail": short_output(result.output),
    }


def run_git(args: Sequence[str]) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def tracked_and_untracked_files() -> list[Path]:
    files = set(run_git(["ls-files"]))
    files.update(run_git(["ls-files", "--others", "--exclude-standard"]))
    return sorted((ROOT / file).resolve() for file in files)


def worktree_status() -> dict:
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "status": "dirty" if len(lines) > 1 else "clean",
        "detail": lines,
    }


def discover_file_categories(files: Sequence[Path]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {
        "wslScripts": [],
        "policyFiles": [],
        "schemaFiles": [],
        "evalFiles": [],
        "browserTests": [],
        "plans": [],
    }
    for path in files:
        try:
            rel = relative(path)
        except ValueError:
            continue
        if rel.startswith("scripts/wsl/") and path.suffix == ".py":
            categories["wslScripts"].append(rel)
        elif rel.startswith("policies/") and path.suffix == ".json":
            categories["policyFiles"].append(rel)
        elif rel.startswith("schemas/") and path.suffix == ".json":
            categories["schemaFiles"].append(rel)
        elif rel.startswith("evals/"):
            categories["evalFiles"].append(rel)
        elif rel.startswith("tests/browser/"):
            categories["browserTests"].append(rel)
        elif rel.startswith("docs/plans/"):
            categories["plans"].append(rel)
    return {key: sorted(value) for key, value in categories.items()}


def is_repo_python_path(path_text: str) -> bool:
    path = (ROOT / path_text).resolve()
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:
        return False
    return path.suffix == ".py" and (rel.startswith("scripts/wsl/") or rel.startswith("tests/"))


def is_safe_marker_command(command: Sequence[str]) -> bool:
    if not command:
        return False
    executable = command[0]
    if executable not in {"python", "python3"}:
        return False
    if len(command) >= 3 and command[1:3] == ["-m", "py_compile"]:
        return all(is_repo_python_path(item) for item in command[3:])
    if len(command) >= 2 and command[1] in {"-c", "-m"}:
        return False
    flags = [item.split("=", 1)[0] for item in command[2:] if item.startswith("--")]
    if any(flag in FORBIDDEN_MARKER_FLAGS for flag in flags):
        return False
    if any(flag not in ALLOWED_MARKER_FLAGS for flag in flags):
        return False
    return len(command) >= 2 and is_repo_python_path(command[1])


def parse_autoloop_markers(path: Path) -> tuple[list[CommandSpec], list[dict]]:
    specs: list[CommandSpec] = []
    ignored: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return specs, ignored
    except OSError as exc:
        ignored.append({"path": relative(path), "reason": str(exc)})
        return specs, ignored

    for line_number, line in enumerate(lines[:30], start=1):
        marker_index = line.find(MARKER_PREFIX)
        if marker_index < 0:
            continue
        marker = line[marker_index + len(MARKER_PREFIX) :].strip()
        if not marker.startswith("command="):
            ignored.append({"path": relative(path), "line": line_number, "reason": "missing command="})
            continue
        raw_command = marker.removeprefix("command=").strip()
        try:
            command = shlex.split(raw_command)
        except ValueError as exc:
            ignored.append({"path": relative(path), "line": line_number, "reason": str(exc)})
            continue
        if not is_safe_marker_command(command):
            ignored.append({"path": relative(path), "line": line_number, "reason": "unsafe marker command"})
            continue
        name = f"marker:{relative(path)}:{line_number}"
        specs.append(CommandSpec(name=name, command=command, timeout=60, source="marker"))
    return specs, ignored


def discover_marker_commands(files: Sequence[Path]) -> tuple[list[CommandSpec], list[dict]]:
    specs: list[CommandSpec] = []
    ignored: list[dict] = []
    for path in files:
        if path.suffix != ".py":
            continue
        file_specs, file_ignored = parse_autoloop_markers(path)
        specs.extend(file_specs)
        ignored.extend(file_ignored)
    return specs, ignored


def existing_paths(paths: Sequence[str]) -> list[str]:
    return [path for path in paths if (ROOT / path).exists()]


def base_specs(files: Sequence[Path], include_browser_probe: bool) -> list[CommandSpec]:
    categories = discover_file_categories(files)
    specs: list[CommandSpec] = []

    wsl_scripts = categories["wslScripts"]
    if wsl_scripts:
        specs.append(
            CommandSpec(
                name="python:compile:wsl",
                command=["python3", "-m", "py_compile", *wsl_scripts],
                timeout=60,
            )
        )

    policy_files = existing_paths(
        [
            "policies/openclaw.exec-approvals.template.json",
            "policies/windows-node.exec-policy.template.json",
        ]
    )
    if policy_files and (ROOT / "tests/policy/validate_policy.py").exists():
        specs.append(
            CommandSpec(
                name="policy:validate",
                command=["python3", "tests/policy/validate_policy.py", *policy_files],
                timeout=30,
            )
        )

    schema_files = existing_paths(
        [
            "schemas/approval-request.schema.json",
            "schemas/openclaw-action-request.schema.json",
        ]
    )
    if schema_files and (ROOT / "tests/policy/validate_schemas.py").exists():
        specs.append(
            CommandSpec(
                name="schema:validate",
                command=["python3", "tests/policy/validate_schemas.py", *schema_files],
                timeout=30,
            )
        )

    if (ROOT / "scripts/wsl/approval_queue.py").exists():
        specs.append(
            CommandSpec(
                name="approval:status",
                command=["python3", "scripts/wsl/approval_queue.py", "status"],
                timeout=20,
                required=False,
            )
        )

    if (ROOT / "scripts/wsl/e2e_dry_run.py").exists():
        for name, intent in DEFAULT_INTENTS.items():
            specs.append(
                CommandSpec(
                    name=f"intent:{name}",
                    command=["python3", "scripts/wsl/e2e_dry_run.py", "--intent", intent],
                    timeout=20,
                )
            )

    if (ROOT / "scripts/wsl/debug_monitor.py").exists():
        specs.append(
            CommandSpec(
                name="debug:monitor",
                command=["python3", "scripts/wsl/debug_monitor.py", "--once"],
                timeout=90,
            )
        )

    if include_browser_probe and (ROOT / "scripts/wsl/browser_probe.py").exists():
        specs.append(
            CommandSpec(
                name="browser:probe",
                command=["python3", "scripts/wsl/browser_probe.py", "--no-open"],
                timeout=180,
                slow=True,
                required=False,
            )
        )

    return specs


def should_run_browser_probe(cycle_index: int, probe_every: int, results: Sequence[CommandResult]) -> bool:
    if probe_every <= 0:
        return False
    monitor = next((result for result in results if result.spec.name == "debug:monitor"), None)
    if monitor is None:
        return False
    browser_blocked = "- browser: blocked" in monitor.output or "browser live check unavailable" in monitor.output
    return browser_blocked and (cycle_index == 1 or cycle_index % probe_every == 0)


def dedupe_specs(specs: Sequence[CommandSpec]) -> list[CommandSpec]:
    seen: set[str] = set()
    unique: list[CommandSpec] = []
    for spec in specs:
        key = json.dumps(spec.command, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


def summarize_results(results: Sequence[CommandResult]) -> str:
    if any(result.status == "blocked" for result in results):
        return "blocked"
    if any(result.status == "warning" for result in results):
        return "warning"
    return "ok"


def run_cycle(args: argparse.Namespace, cycle_index: int) -> dict:
    files = tracked_and_untracked_files()
    categories = discover_file_categories(files)
    marker_specs, ignored_markers = discover_marker_commands(files)
    specs = base_specs(files, include_browser_probe=args.include_slow)
    specs.extend(marker_specs)
    specs = [spec for spec in dedupe_specs(specs) if args.include_slow or not spec.slow]

    results = [run_command(spec) for spec in specs]
    if should_run_browser_probe(cycle_index, args.probe_every, results):
        probe = CommandSpec(
            name="browser:probe:on_blocked",
            command=["python3", "scripts/wsl/browser_probe.py", "--no-open"],
            timeout=180,
            slow=True,
            required=False,
            source="blocked-browser",
        )
        results.append(run_command(probe))

    return {
        "timestamp": now_iso(),
        "cycle": cycle_index,
        "mode": "safe_autoloop",
        "status": summarize_results(results),
        "worktree": worktree_status(),
        "discovered": {
            "fileCount": len(files),
            "categories": categories,
            "markerCount": len(marker_specs),
            "ignoredMarkers": ignored_markers,
        },
        "results": [result_record(result) for result in results],
    }


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Debug Autoloop Summary",
        "",
        f"- timestamp: {record['timestamp']}",
        f"- cycle: {record['cycle']}",
        f"- status: {record['status']}",
        f"- worktree: {record['worktree']['status']}",
        f"- files: {record['discovered']['fileCount']}",
        f"- markers: {record['discovered']['markerCount']}",
        "",
        "## Results",
    ]
    for result in record["results"]:
        lines.append(f"- {result['name']}: {result['status']} ({result['returncode']})")
        if result["status"] != "ok" and result["detail"]:
            lines.append(f"  - detail: {result['detail']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_record(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"cycle: {record['cycle']}")
    print(f"worktree: {record['worktree']['status']}")
    print(f"files: {record['discovered']['fileCount']}")
    for result in record["results"]:
        print(f"- {result['name']}: {result['status']} ({result['returncode']})")
        if result["status"] != "ok" and result["detail"]:
            print(f"  detail: {result['detail']}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-discovering safe debug loop for OpenClaw voice work.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one autoloop cycle.")
    mode.add_argument("--watch", action="store_true", help="Run repeated autoloop cycles.")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between watch cycles.")
    parser.add_argument("--iterations", type=int, default=0, help="Watch iterations; 0 means forever.")
    parser.add_argument("--include-slow", action="store_true", help="Always include slow probes.")
    parser.add_argument("--probe-every", type=int, default=3, help="Run browser probe every N blocked cycles.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="JSONL autoloop log path.")
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_SUMMARY_PATH, help="Latest markdown summary path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    watch = args.watch
    iterations = args.iterations if watch else 1
    count = 0
    last_record: dict | None = None

    while True:
        count += 1
        record = run_cycle(args, count)
        append_jsonl(args.log_path, record)
        write_summary(args.summary_path, record)
        print_record(record)
        last_record = record

        if not watch or (iterations > 0 and count >= iterations):
            break
        time.sleep(max(args.interval, 0))

    return 1 if last_record and last_record["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
