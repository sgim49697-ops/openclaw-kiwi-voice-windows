# browser_interact_probe.py - safe OpenClaw browser interact smoke probe.
from __future__ import annotations

import argparse
import contextlib
import functools
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "browser" / "fixtures" / "interact-smoke.html"
DEFAULT_LOG_PATH = ROOT / ".debugloop" / "runs" / "latest.jsonl"
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "browser"
DEFAULT_PROFILE = "windows-cdp"

HIGH_IMPACT_COMMANDS = {"submit", "send", "upload", "download", "payment", "purchase", "delete"}


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


def short_output(output: str, limit: int = 1400) -> str:
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


def command_record(result: CommandResult, required: bool = True, expected_text: str | None = None) -> dict:
    ok = result.returncode == 0
    return {
        "name": result.name,
        "status": "ok" if ok else ("blocked" if required else "warning"),
        "required": required,
        "returncode": result.returncode,
        "durationMs": result.duration_ms,
        "command": result.command,
        "expectedText": expected_text,
        "detail": short_output(result.output),
    }


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def media_path_from_output(output: str) -> Path | None:
    match = re.search(r"MEDIA:([^\s]+)", output)
    if not match:
        return None
    raw_path = match.group(1).strip()
    if raw_path.startswith("~/"):
        return Path.home() / raw_path[2:]
    return Path(raw_path)


def copy_screenshot_artifact(output: str) -> str | None:
    media_path = media_path_from_output(output)
    if media_path is None or not media_path.exists():
        return None

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = media_path.suffix or ".png"
    out_path = ARTIFACT_DIR / f"browser-interact-ok{suffix}"
    shutil.copy2(media_path, out_path)
    return str(out_path.relative_to(ROOT))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextlib.contextmanager
def fixture_server() -> Iterator[str]:
    if not FIXTURE_PATH.exists():
        raise FileNotFoundError(f"Missing fixture: {FIXTURE_PATH}")

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    port = free_port()
    handler = functools.partial(QuietHandler, directory=str(FIXTURE_PATH.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="browser-interact-fixture", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/{FIXTURE_PATH.name}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def extract_ref(snapshot: str, label: str) -> str | None:
    candidates = [line for line in snapshot.splitlines() if label in line]
    patterns = [
        re.compile(r"\[ref[=:]\s*([^\]\s]+)\]"),
        re.compile(r"\bref[=:]\s*[\"']?([A-Za-z0-9_-]+)"),
        re.compile(r"\[([A-Za-z0-9_-]+)\].*" + re.escape(label)),
        re.compile(re.escape(label) + r".*\[([A-Za-z0-9_-]+)\]"),
    ]

    for line in candidates:
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                return match.group(1).strip("\"'")
    return None


def extract_tab_label(output: str) -> str | None:
    match = re.search(r"\btab:\s*(t\d+)\b", output)
    return match.group(1) if match else None


def ensure_expected(snapshot: CommandResult, expected_text: str) -> CommandResult:
    if snapshot.returncode != 0:
        return snapshot
    if expected_text in snapshot.output:
        return snapshot
    return CommandResult(
        snapshot.name,
        snapshot.command,
        2,
        f"expected text not found: {expected_text}\n\n{snapshot.output}",
        snapshot.duration_ms,
    )


def blocked_record(args: argparse.Namespace, checks: list[dict], failed_at: str, reason: str, fixture_url: str | None = None) -> dict:
    return {
        "timestamp": now_iso(),
        "mode": "browser_interact_probe",
        "profile": args.profile,
        "fixtureUrl": fixture_url,
        "status": "blocked",
        "failedAt": failed_at,
        "reason": reason,
        "checks": checks,
        "artifacts": {},
    }


def validate_args(args: argparse.Namespace) -> None:
    if args.profile != DEFAULT_PROFILE:
        raise ValueError(f"profile must be {DEFAULT_PROFILE}; refusing to use {args.profile}")


def assert_no_high_impact(command: Sequence[str]) -> None:
    lowered = {part.lower() for part in command}
    overlap = lowered & HIGH_IMPACT_COMMANDS
    if overlap:
        raise ValueError(f"high-impact browser command is not allowed: {sorted(overlap)}")


def run_safe(name: str, command: Sequence[str], timeout: int = 45) -> CommandResult:
    assert_no_high_impact(command)
    return run_command(name, command, timeout=timeout)


def cdp_ready(url: str = "http://127.0.0.1:9222/json/version", timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def ensure_windows_cdp(args: argparse.Namespace, checks: list[dict]) -> None:
    if args.profile != "windows-cdp":
        return
    if args.no_ensure_cdp or cdp_ready():
        return

    powershell = shutil.which("powershell.exe")
    if not powershell:
        raise RuntimeError("windows-cdp is not running and powershell.exe is unavailable")

    command = [
        powershell,
        "-NoProfile",
        "-Command",
        (
            "$chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'; "
            "$profile = Join-Path $env:LOCALAPPDATA 'OpenClawBrowserCdp\\windows-cdp-profile'; "
            "if (-not (Test-Path $chrome)) { throw 'Chrome executable not found' }; "
            "New-Item -ItemType Directory -Force -Path $profile | Out-Null; "
            "Start-Process -FilePath $chrome -ArgumentList @("
            "'--remote-debugging-port=9222', "
            "'--remote-debugging-address=127.0.0.1', "
            "\"--user-data-dir=$profile\", "
            "'--no-first-run', "
            "'--no-default-browser-check', "
            "'--disable-sync', "
            "'--disable-background-networking', "
            "'--disable-features=Translate,MediaRouter', "
            "'about:blank'"
            "); "
            "Write-Output $profile"
        ),
    ]
    result = run_command("ensure_windows_cdp", command, timeout=20)
    checks.append(command_record(result, required=True))
    if result.returncode != 0:
        raise RuntimeError(result.output)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if cdp_ready():
            return
        time.sleep(0.25)
    raise RuntimeError("windows-cdp did not become ready on http://127.0.0.1:9222")


def build_probe(args: argparse.Namespace) -> dict:
    validate_args(args)
    checks: list[dict] = []
    artifacts: dict[str, str] = {}

    ensure_windows_cdp(args, checks)

    with fixture_server() as fixture_url:
        preflight_steps = [
            ("status", browser_command(args.profile, "status"), True, 30),
            ("open", browser_command(args.profile, "open", fixture_url), True, 30),
            ("initial_snapshot", browser_command(args.profile, "snapshot", "--labels", "--limit", str(args.snapshot_limit)), True, 45),
        ]

        initial_snapshot: CommandResult | None = None
        for name, command, required, timeout in preflight_steps:
            result = run_safe(name, command, timeout=timeout)
            checks.append(command_record(result, required=required))
            if name == "open" and result.returncode == 0:
                tab_label = extract_tab_label(result.output)
                if tab_label:
                    focus = run_safe("focus_fixture", browser_command(args.profile, "focus", tab_label), timeout=30)
                    checks.append(command_record(focus, required=True))
                    if focus.returncode != 0:
                        return blocked_record(args, checks, "focus_fixture", focus.output, fixture_url=fixture_url)
            if name == "initial_snapshot":
                initial_snapshot = result
            if required and result.returncode != 0:
                return blocked_record(args, checks, name, result.output, fixture_url=fixture_url)

        assert initial_snapshot is not None
        refs = {
            "click": extract_ref(initial_snapshot.output, "Smoke Click Button"),
            "type": extract_ref(initial_snapshot.output, "Smoke Type Input"),
            "fill": extract_ref(initial_snapshot.output, "Smoke Fill Input"),
            "select": extract_ref(initial_snapshot.output, "Smoke Select Box"),
        }
        missing_refs = [name for name, ref in refs.items() if not ref]
        if missing_refs:
            return blocked_record(
                args,
                checks,
                "ref_extract",
                f"missing refs for {missing_refs}; snapshot={short_output(initial_snapshot.output, limit=3000)}",
                fixture_url=fixture_url,
            )

        interactions: list[tuple[str, list[str], str]] = [
            ("click", browser_command(args.profile, "click", refs["click"]), "click: Smoke Click Button clicked"),
            ("type", browser_command(args.profile, "type", refs["type"], "typed-smoke"), "type: typed-smoke"),
            (
                "fill",
                browser_command(args.profile, "fill", "--fields", json.dumps([{"ref": refs["fill"], "value": "filled-smoke"}])),
                "fill: filled-smoke",
            ),
            ("select", browser_command(args.profile, "select", refs["select"], "Bravo Option"), "select: bravo"),
        ]

        for name, command, expected_text in interactions:
            result = run_safe(name, command, timeout=45)
            checks.append(command_record(result, required=True, expected_text=expected_text))
            if result.returncode != 0:
                return blocked_record(args, checks, name, result.output, fixture_url=fixture_url)

            snapshot = run_safe(f"{name}_snapshot", browser_command(args.profile, "snapshot", "--labels", "--limit", str(args.snapshot_limit)), timeout=45)
            snapshot = ensure_expected(snapshot, expected_text)
            checks.append(command_record(snapshot, required=True, expected_text=expected_text))
            if snapshot.returncode != 0:
                return blocked_record(args, checks, f"{name}_snapshot", snapshot.output, fixture_url=fixture_url)

        screenshot = run_safe("screenshot", browser_command(args.profile, "screenshot"), timeout=45)
        checks.append(command_record(screenshot, required=True))
        if screenshot.returncode != 0:
            return blocked_record(args, checks, "screenshot", screenshot.output, fixture_url=fixture_url)
        screenshot_artifact = copy_screenshot_artifact(screenshot.output)
        if screenshot_artifact:
            artifacts["screenshot"] = screenshot_artifact

        return {
            "timestamp": now_iso(),
            "mode": "browser_interact_probe",
            "profile": args.profile,
            "fixtureUrl": fixture_url,
            "status": "ok",
            "failedAt": None,
            "refs": refs,
            "checks": checks,
            "artifacts": artifacts,
        }


def print_probe(record: dict) -> None:
    print(f"status: {record['status']}")
    print(f"profile: {record['profile']}")
    if record.get("fixtureUrl"):
        print(f"fixtureUrl: {record['fixtureUrl']}")
    if record.get("failedAt"):
        print(f"failedAt: {record['failedAt']}")
    if record.get("reason"):
        print(f"reason: {short_output(record['reason'])}")
    for check in record["checks"]:
        print(f"- {check['name']}: {check['status']} ({check['durationMs']}ms)")
        if check["status"] != "ok" and check.get("detail"):
            print(f"  detail: {check['detail']}")
    for name, path in record.get("artifacts", {}).items():
        print(f"artifact[{name}]: {path}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a safe OpenClaw browser interact smoke probe.")
    parser.add_argument("--profile", default=DEFAULT_PROFILE, help=f"Browser profile to probe; must be {DEFAULT_PROFILE}.")
    parser.add_argument("--snapshot-limit", type=int, default=400, help="Snapshot output limit.")
    parser.add_argument("--no-ensure-cdp", action="store_true", help="Do not auto-start the dedicated Windows CDP Chrome.")
    parser.add_argument("--no-write-log", action="store_true", help="Do not append probe result to JSONL log.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="JSONL log path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        record = build_probe(args)
    except Exception as exc:
        record = {
            "timestamp": now_iso(),
            "mode": "browser_interact_probe",
            "profile": args.profile,
            "fixtureUrl": None,
            "status": "blocked",
            "failedAt": "startup",
            "reason": str(exc),
            "checks": [],
            "artifacts": {},
        }
    print_probe(record)
    if not args.no_write_log:
        append_jsonl(args.log_path, record)
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
