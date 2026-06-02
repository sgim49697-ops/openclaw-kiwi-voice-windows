# codex_plan_probe.py - build and optionally run v6 Codex plan dispatcher probes.
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
REPO_DISPATCHER = ROOT / "scripts" / "win" / "Invoke-OpenClawAction.ps1"
DEPLOYED_DISPATCHER = r"C:\OpenClawActions\Invoke-OpenClawAction.ps1"
DEFAULT_PROJECT_PATH = r"\\wsl.localhost\Ubuntu-22.04\home\user\projects\openclaw-kiwi-voice-windows"
DEFAULT_TASK = "Create a read-only implementation plan for the current project."
NEGATIVE_CASES = {"outside-root", "hash-mismatch", "missing-approval"}


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def base64url_json(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_request(args: argparse.Namespace) -> dict[str, Any]:
    project_path = args.project_path
    approved = True

    if args.case == "outside-root":
        project_path = r"C:\Windows"
    elif args.case == "missing-approval":
        approved = False

    params = {
        "projectPath": project_path,
        "task": args.task,
    }
    payload = {
        "version": 1,
        "requestId": args.request_id,
        "source": "codex-plan-probe",
        "riskTier": "medium",
        "action": "open_vscode_codex_plan",
        "params": params,
    }
    request = {
        **payload,
        "approvedByUser": approved,
        "approvalMethod": "manual",
        "payloadHash": payload_hash(payload),
    }

    if args.case == "hash-mismatch":
        request["payloadHash"] = "sha256:" + ("0" * 64)

    return request


def wslpath(path: Path) -> str:
    completed = subprocess.run(
        ["wslpath", "-w", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def dispatcher_path(args: argparse.Namespace) -> str:
    if args.dispatcher == "deployed":
        return DEPLOYED_DISPATCHER
    return wslpath(REPO_DISPATCHER)


def run_dispatcher(args: argparse.Namespace, encoded_request: str) -> dict[str, Any]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        dispatcher_path(args),
        "-RequestJsonBase64",
        encoded_request,
    ]
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    expected_success = args.case not in NEGATIVE_CASES
    passed = completed.returncode == 0 if expected_success else completed.returncode != 0
    return {
        "command": command,
        "returnCode": completed.returncode,
        "expectedSuccess": expected_success,
        "passed": passed,
        "stdout": completed.stdout.decode("utf-8", errors="replace").strip(),
        "stderr": completed.stderr.decode("utf-8", errors="replace").strip(),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and optionally run v6 Codex plan dispatcher probes.")
    parser.add_argument(
        "--case",
        choices=("positive", "outside-root", "hash-mismatch", "missing-approval"),
        default="positive",
        help="Probe case to build.",
    )
    parser.add_argument("--request-id", default="v6-codex-plan-probe")
    parser.add_argument("--project-path", default=DEFAULT_PROJECT_PATH)
    parser.add_argument("--task", default=DEFAULT_TASK)
    parser.add_argument("--dispatcher", choices=("repo", "deployed"), default="repo")
    parser.add_argument("--execute", action="store_true", help="Run the generated request through PowerShell.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    request = build_request(args)
    encoded_request = base64url_json(request)
    output: dict[str, Any] = {
        "case": args.case,
        "dispatcher": args.dispatcher,
        "request": request,
        "requestJsonBase64": encoded_request,
    }

    if args.execute:
        execution = run_dispatcher(args, encoded_request)
        output["execution"] = execution
        print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if execution["passed"] else 1

    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
