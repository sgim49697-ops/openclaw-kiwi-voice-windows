# browser_probe.py - read-only OpenClaw browser lane diagnostic probe.
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_PATH = ROOT / ".debugloop" / "runs" / "latest.jsonl"
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "browser"
DEFAULT_URL = "https://example.com"


@dataclass
class CommandResult:
    name: str
    command: list[str]
    returncode: int
    output: str
    duration_ms: int


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    npm_global = str(Path.home() / ".npm-global" / "bin")
    env["PATH"] = f"{npm_global}{os.pathsep}{env.get('PATH', '')}"
    return env


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def short_output(output: str, limit: int = 1200) -> str:
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def run_command(name: str, command: Sequence[str], timeout: int = 45) -> CommandResult:
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
        return CommandResult(name, list(command), completed.returncode, output, int((time.monotonic() - started) * 1000))
    except FileNotFoundError as exc:
        return CommandResult(name, list(command), 127, str(exc), int((time.monotonic() - started) * 1000))
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CommandResult(name, list(command), 124, output.strip() or "command timed out", int((time.monotonic() - started) * 1000))


def browser_command(profile: str, *args: str) -> list[str]:
    return ["openclaw", "browser", "--browser-profile", profile, *args]


def command_record(result: CommandResult, required: bool = True) -> dict:
    ok = result.returncode == 0
    return {
        "name": result.name,
        "status": "ok" if ok else ("blocked" if required else "warning"),
        "required": required,
        "returncode": result.returncode,
        "durationMs": result.duration_ms,
        "command": result.command,
        "detail": short_output(result.output),
    }


def gateway_log_path() -> Path | None:
    result = run_command("gateway_status_for_log", ["openclaw", "gateway", "status"], timeout=20)
    match = re.search(r"File logs:\s*(.+)", result.output)
    if not match:
        return None
    raw_path = match.group(1).strip()
    if raw_path.startswith("~/"):
        return Path.home() / raw_path[2:]
    return Path(raw_path)


def write_gateway_log_excerpt(max_lines: int = 80, max_chars: int = 40000) -> str | None:
    path = gateway_log_path()
    if path is None or not path.exists():
        return None

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    excerpt = "\n".join(lines[-max_lines:])
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = ARTIFACT_DIR / "gateway-log-excerpt.txt"
    out_path.write_text(excerpt + "\n", encoding="utf-8")
    return str(out_path.relative_to(ROOT))


def extract_media_path(output: str) -> Path | None:
    match = re.search(r"MEDIA:([^\s]+)", output)
    if not match:
        return None
    raw_path = match.group(1).strip()
    if raw_path.startswith("~/"):
        return Path.home() / raw_path[2:]
    return Path(raw_path)


def copy_screenshot_artifact(output: str) -> str | None:
    media_path = extract_media_path(output)
    if media_path is None or not media_path.exists():
        return None

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = media_path.suffix or ".png"
    out_path = ARTIFACT_DIR / f"browser-read-ok{suffix}"
    shutil.copy2(media_path, out_path)
    return str(out_path.relative_to(ROOT))


def build_probe(args: argparse.Namespace) -> dict:
    checks: list[dict] = []
    screenshot_artifact: str | None = None

    steps: list[tuple[str, list[str], bool, int]] = [
        ("status", browser_command(args.profile, "status"), True, 30),
        ("tabs", browser_command(args.profile, "tabs"), True, 30),
    ]
    if not args.no_open:
        steps.append(("open", browser_command(args.profile, "open", args.url), True, 30))
    if not args.skip_doctor:
        steps.append(("doctor", browser_command(args.profile, "doctor", "--deep"), True, 45))
    if not args.skip_snapshot:
        steps.append(("snapshot", browser_command(args.profile, "snapshot", "--format", "aria", "--limit", str(args.snapshot_limit)), True, 45))
    if not args.skip_screenshot:
        steps.append(("screenshot", browser_command(args.profile, "screenshot"), True, 45))
    steps.extend(
        [
            ("console", browser_command(args.profile, "console"), False, 45),
            ("errors", browser_command(args.profile, "errors"), False, 45),
        ]
    )

    for name, command, required, timeout in steps:
        result = run_command(name, command, timeout=timeout)
        checks.append(command_record(result, required=required))
        if name == "screenshot" and result.returncode == 0:
            screenshot_artifact = copy_screenshot_artifact(result.output)

    required_failures = [check for check in checks if check["required"] and check["status"] != "ok"]
    artifacts = {
        "gatewayLogExcerpt": write_gateway_log_excerpt() if required_failures else None,
        "screenshot": screenshot_artifact,
    }
    artifacts = {key: value for key, value in artifacts.items() if value}

    return {
        "timestamp": now_iso(),
        "mode": "browser_probe",
        "profile": args.profile,
        "url": args.url,
        "status": "blocked" if required_failures else "ok",
        "failedAt": required_failures[0]["name"] if required_failures else None,
        "checks": checks,
        "artifacts": artifacts,
    }


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def print_probe(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"profile: {record['profile']}")
    print(f"url: {record['url']}")
    if record.get("failedAt"):
        print(f"failedAt: {record['failedAt']}")
    for check in record["checks"]:
        print(f"- {check['name']}: {check['status']} ({check['durationMs']}ms)")
        if check["status"] != "ok" and check.get("detail"):
            print(f"  detail: {check['detail']}")
    for name, path in record.get("artifacts", {}).items():
        print(f"artifact[{name}]: {path}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a read-only browser lane diagnostic probe.")
    parser.add_argument("--profile", default="openclaw", help="Browser profile to probe.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Safe read-only URL to open.")
    parser.add_argument("--snapshot-limit", type=int, default=200, help="Snapshot output limit.")
    parser.add_argument("--no-open", action="store_true", help="Skip opening the URL before probing live commands.")
    parser.add_argument("--skip-doctor", action="store_true", help="Skip the deep browser doctor step.")
    parser.add_argument("--skip-snapshot", action="store_true", help="Skip the OpenClaw browser snapshot step.")
    parser.add_argument("--skip-screenshot", action="store_true", help="Skip the OpenClaw browser screenshot step.")
    parser.add_argument("--no-write-log", action="store_true", help="Do not append probe result to JSONL log.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="JSONL log path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    record = build_probe(args)
    print_probe(record)
    if not args.no_write_log:
        append_jsonl(args.log_path, record)
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
