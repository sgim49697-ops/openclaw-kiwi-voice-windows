# kiwi_windows_probe.py - inspect Windows Kiwi Voice readiness without changing host state.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any, Sequence


DEFAULT_KIWI_PATH = r"C:\Users\ksg63\projects\kiwi-voice"
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:7789"
DEFAULT_DRY_RUN_SHIM = DEFAULT_KIWI_PATH + r"\dry-run-openclaw.cmd"


def run_powershell(script: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    path_refresh = (
        "$machinePath=[Environment]::GetEnvironmentVariable('Path','Machine'); "
        "$userPath=[Environment]::GetEnvironmentVariable('Path','User'); "
        "$env:Path=($machinePath,$userPath,$env:Path -join ';'); "
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", path_refresh + script],
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def get_command_inventory() -> list[dict[str, Any]]:
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$names=@('uv','python','py','git','ffmpeg','openclaw','kiwi','nvidia-smi'); "
        "Get-Command $names | "
        "Select-Object Name,Source,Version | ConvertTo-Json -Compress"
    )
    completed = run_powershell(script)
    if not completed.stdout.strip():
        return []
    data = json.loads(completed.stdout)
    return data if isinstance(data, list) else [data]


def test_path(path: str) -> bool:
    script = f"Test-Path -LiteralPath {json.dumps(path)}"
    completed = run_powershell(script)
    return completed.stdout.strip().lower() == "true"


def read_kiwi_env(kiwi_path: str) -> dict[str, str]:
    env_path = kiwi_path + r"\.env"
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$path={json.dumps(env_path)}; "
        "if (-not (Test-Path -LiteralPath $path)) { '{}' } else { "
        "$items=@{}; "
        "Get-Content -LiteralPath $path | ForEach-Object { "
        "$line=$_.Trim(); "
        "if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) { "
        "$parts=$line.Split('=',2); $items[$parts[0].Trim()]=$parts[1].Trim() "
        "} }; "
        "$items | ConvertTo-Json -Compress }"
    )
    completed = run_powershell(script)
    if not completed.stdout.strip():
        return {}
    data = json.loads(completed.stdout)
    return data if isinstance(data, dict) else {}


def probe_dashboard(url: str) -> dict[str, Any]:
    script = (
        "$ErrorActionPreference='Stop'; "
        f"$uri={json.dumps(url)}; "
        "try { "
        "$response=Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 3; "
        "[pscustomobject]@{reachable=$true; status=[int]$response.StatusCode; url=$uri} | ConvertTo-Json -Compress "
        "} catch { "
        "$type=$_.Exception.GetType().FullName; "
        "[pscustomobject]@{reachable=$false; error='dashboard request failed'; errorType=$type; url=$uri} | ConvertTo-Json -Compress "
        "}"
    )
    completed = run_powershell(script, timeout=8)
    if completed.stdout.strip():
        return json.loads(completed.stdout)
    return {"reachable": False, "error": completed.stderr.strip() or "unknown", "url": url}


def command_map(commands: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("Name", "")).lower(): item for item in commands}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    commands = get_command_inventory()
    commands_by_name = command_map(commands)
    kiwi_root_exists = test_path(args.kiwi_path)
    venv_exists = test_path(args.kiwi_path + r"\venv\Scripts\python.exe")
    config_exists = test_path(args.kiwi_path + r"\config.yaml")
    env_exists = test_path(args.kiwi_path + r"\.env")
    dry_run_shim_exists = test_path(args.dry_run_shim)
    kiwi_env = read_kiwi_env(args.kiwi_path) if env_exists else {}
    dashboard = probe_dashboard(args.dashboard_url)

    blockers: list[str] = []
    if "uv.exe" not in commands_by_name:
        blockers.append("uv is not available on Windows PATH")
    if "python.exe" not in commands_by_name and "py.exe" not in commands_by_name:
        blockers.append("python is not available on Windows PATH")
    if "git.exe" not in commands_by_name:
        blockers.append("git is not available on Windows PATH")
    if "ffmpeg.exe" not in commands_by_name:
        blockers.append("ffmpeg is not available on Windows PATH")
    if "openclaw.cmd" not in commands_by_name and "openclaw.ps1" not in commands_by_name:
        blockers.append("openclaw CLI is not available on Windows PATH")
    if not kiwi_root_exists:
        blockers.append(f"Kiwi repo is not cloned at {args.kiwi_path}")
    elif not venv_exists:
        blockers.append("Kiwi virtual environment is not created")

    openclaw_bin_is_dry_run = kiwi_env.get("OPENCLAW_BIN") == args.dry_run_shim
    if blockers:
        next_manual_steps = [
            "Install FFmpeg and ensure ffmpeg.exe is on PATH if missing.",
            "Install OpenClaw CLI on Windows if missing.",
            "Clone Kiwi Voice to the fixed Windows path.",
            "Run uv venv venv and uv pip install -r requirements.txt from the Kiwi repo.",
            "Copy .env.example to .env and keep secrets out of this repo.",
        ]
    elif dashboard.get("reachable") and openclaw_bin_is_dry_run:
        next_manual_steps = [
            "Run python3 scripts/wsl/kiwi_live_dry_run_probe.py before microphone smoke tests.",
            "Use the Kiwi dashboard microphone only for v7.2 dry-run phrases.",
        ]
    elif dashboard.get("reachable"):
        next_manual_steps = [
            "Set OPENCLAW_BIN to the dry-run shim before microphone smoke tests.",
            "Run python3 scripts/wsl/kiwi_live_dry_run_probe.py after updating .env.",
        ]
    else:
        next_manual_steps = [
            "Run python -m kiwi from the Windows Kiwi repo.",
            "Open http://127.0.0.1:7789 and verify the dashboard before v7.2 microphone work.",
        ]

    return {
        "status": "blocked" if blockers else "ready",
        "kiwiPath": args.kiwi_path,
        "commands": commands,
        "paths": {
            "kiwiRoot": kiwi_root_exists,
            "venvPython": venv_exists,
            "configYaml": config_exists,
            "envFile": env_exists,
            "dryRunShim": dry_run_shim_exists,
        },
        "kiwiEnv": {
            "KIWI_WS_ENABLED": kiwi_env.get("KIWI_WS_ENABLED"),
            "OPENCLAW_BIN": kiwi_env.get("OPENCLAW_BIN"),
            "openclawBinIsDryRunShim": openclaw_bin_is_dry_run,
        },
        "dashboard": dashboard,
        "blockers": blockers,
        "nextManualSteps": next_manual_steps,
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect Windows Kiwi Voice readiness without mutating host state.")
    parser.add_argument("--kiwi-path", default=DEFAULT_KIWI_PATH)
    parser.add_argument("--dashboard-url", default=DEFAULT_DASHBOARD_URL)
    parser.add_argument("--dry-run-shim", default=DEFAULT_DRY_RUN_SHIM)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when readiness is blocked.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    report = build_report(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and report["status"] != "ready":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
