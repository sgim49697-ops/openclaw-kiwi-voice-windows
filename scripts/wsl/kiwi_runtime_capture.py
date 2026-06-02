# kiwi_runtime_capture.py - restart Windows Kiwi with stdout/stderr captured to ignored artifacts.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "kiwi"
DEFAULT_KIWI_PATH = r"C:\Users\ksg63\projects\kiwi-voice"
DEFAULT_ENV_PATH = Path("/mnt/c/Users/ksg63/projects/kiwi-voice/.env")
DEFAULT_DRY_RUN_SHIM = DEFAULT_KIWI_PATH + r"\dry-run-openclaw.cmd"
WATCH_TERMS = ("WEB_AUDIO", "Speech segment", "External audio submitted", "PROCESS", "WHISPER", "WAKE", "OPENCLAW")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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


def ensure_safe_env(env_path: Path, shim_path: str) -> list[str]:
    env = read_env(env_path)
    errors: list[str] = []
    if env.get("OPENCLAW_BIN") != shim_path:
        errors.append("OPENCLAW_BIN does not point to dry-run shim")
    if env.get("KIWI_WS_ENABLED", "").lower() != "false":
        errors.append("KIWI_WS_ENABLED must remain false")
    return errors


def stop_kiwi(kiwi_path: str) -> dict:
    script = (
        "$kiwi=" + ps_literal(kiwi_path) + "; "
        "$matches=Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -in @('python.exe','cmd.exe') -and $_.CommandLine -and $_.CommandLine.Contains($kiwi) -and $_.CommandLine.Contains('-m kiwi') "
        "}; "
        "$ids=@(); "
        "foreach ($proc in $matches) { "
        "$ids += [int]$proc.ProcessId; "
        "Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue "
        "}; "
        "[pscustomobject]@{stopped=$ids} | ConvertTo-Json -Compress"
    )
    completed = run_powershell(script, timeout=30)
    if completed.returncode != 0:
        return {"status": "failed", "stderr": completed.stderr.strip(), "stdout": completed.stdout.strip()}
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        data = {"raw": completed.stdout.strip()}
    data["status"] = "ok"
    return data


def wsl_path_to_unc(path: Path) -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "wslpath failed")
    return completed.stdout.strip()


def start_kiwi(kiwi_path: str, stdout_path: Path, stderr_path: Path) -> dict:
    stdout_unc = wsl_path_to_unc(stdout_path)
    stderr_unc = wsl_path_to_unc(stderr_path)
    python_path = kiwi_path + r"\venv\Scripts\python.exe"
    script = (
        "$kiwi=" + ps_literal(kiwi_path) + "; "
        "$python=" + ps_literal(python_path) + "; "
        "$stdout=" + ps_literal(stdout_unc) + "; "
        "$stderr=" + ps_literal(stderr_unc) + "; "
        "if (-not (Test-Path -LiteralPath $python)) { throw \"Kiwi venv python not found: $python\" }; "
        "$command='cd /d \"' + $kiwi + '\" && \"' + $python + '\" -u -m kiwi 1> \"' + $stdout + '\" 2> \"' + $stderr + '\"'; "
        "$proc=Start-Process -FilePath 'cmd.exe' -ArgumentList @('/d','/c',$command) -WindowStyle Minimized -PassThru; "
        "Start-Sleep -Seconds 3; "
        "[pscustomobject]@{pid=$proc.Id; stdout=$stdout; stderr=$stderr; kiwi=$kiwi} | ConvertTo-Json -Compress"
    )
    completed = run_powershell(script, timeout=45)
    if completed.returncode != 0:
        return {"status": "failed", "stderr": completed.stderr.strip(), "stdout": completed.stdout.strip()}
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        data = {"raw": completed.stdout.strip()}
    data["status"] = "ok"
    return data


def tail_matches(paths: Sequence[Path], limit: int) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
            if any(term in line for term in WATCH_TERMS):
                matches.append({"file": str(path.relative_to(ROOT)), "line": line})
    return matches[-limit:]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restart Windows Kiwi in safe log-capture mode.")
    parser.add_argument("command", choices=("start", "stop", "tail"))
    parser.add_argument("--kiwi-path", default=DEFAULT_KIWI_PATH)
    parser.add_argument("--env-path", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--dry-run-shim", default=DEFAULT_DRY_RUN_SHIM)
    parser.add_argument("--artifact-dir", type=Path, default=ARTIFACT_DIR)
    parser.add_argument("--tail", type=int, default=80)
    parser.add_argument("--record-path", type=Path, default=ARTIFACT_DIR / "kiwi-runtime-current.json")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "timestamp": now_iso(),
        "mode": "kiwi_runtime_capture",
        "command": args.command,
        "status": "ok",
        "kiwiPath": args.kiwi_path,
        "watchTerms": WATCH_TERMS,
    }

    if args.command == "tail":
        if args.record_path.exists():
            previous = json.loads(args.record_path.read_text(encoding="utf-8"))
            paths = [Path(previous.get("stdoutPath", "")), Path(previous.get("stderrPath", ""))]
        else:
            paths = []
        record["matches"] = tail_matches(paths, args.tail)
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if args.command == "stop":
        record["stop"] = stop_kiwi(args.kiwi_path)
        record["status"] = record["stop"].get("status", "failed")
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if record["status"] == "ok" else 1

    env_errors = ensure_safe_env(args.env_path, args.dry_run_shim)
    if env_errors:
        record["status"] = "blocked"
        record["errors"] = env_errors
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    current_stamp = stamp()
    stdout_path = args.artifact_dir / f"kiwi-runtime-{current_stamp}.out.log"
    stderr_path = args.artifact_dir / f"kiwi-runtime-{current_stamp}.err.log"
    record["stop"] = stop_kiwi(args.kiwi_path)
    record["start"] = start_kiwi(args.kiwi_path, stdout_path, stderr_path)
    record["stdoutPath"] = str(stdout_path)
    record["stderrPath"] = str(stderr_path)
    record["status"] = record["start"].get("status", "failed")
    write_json(args.record_path, record)
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if record["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
