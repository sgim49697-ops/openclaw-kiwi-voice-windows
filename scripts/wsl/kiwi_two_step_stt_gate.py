# kiwi_two_step_stt_gate.py - run the v7.2.12 wake-only then command STT dry-run gate.
# debug-autoloop: command=python3 scripts/wsl/kiwi_two_step_stt_gate.py --status
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "kiwi"
DEFAULT_BASE_DIR = ARTIFACT_DIR / "two-step-v7.2.12"
DEFAULT_WAKE_PHRASE = "오픈클로"
DEFAULT_COMMAND_PHRASE = "테스트 알림 보내줘"
CURRENT_CONFIG_CANDIDATE = "small_prompt_openclaw"
OUTPUT_LIMIT = 4000


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run_command(command: Sequence[str], timeout_seconds: int) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        cwd=ROOT,
    )
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "stdout": trim_output(completed.stdout.strip()),
        "stderr": trim_output(completed.stderr.strip()),
    }


def trim_output(value: str) -> str:
    if len(value) <= OUTPUT_LIMIT:
        return value
    return value[:OUTPUT_LIMIT] + f"\n... <truncated {len(value) - OUTPUT_LIMIT} chars; see JSON artifacts> ..."


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_threshold(sample_count: int) -> int:
    return max(2, sample_count // 2 + 1)


def candidate_summary(candidate: dict[str, Any], sample_count: int) -> dict[str, Any]:
    command_hits = int(candidate.get("commandHits") or 0)
    wake_hits = int(candidate.get("wakeHits") or 0)
    return {
        "id": candidate.get("id"),
        "model": candidate.get("model"),
        "prompt": candidate.get("prompt"),
        "status": candidate.get("status"),
        "wakeHits": wake_hits,
        "commandHits": command_hits,
        "hallucinationHits": int(candidate.get("hallucinationHits") or 0),
        "wakePassed": wake_hits > 0,
        "commandStable": command_hits >= stable_threshold(sample_count),
        "passReason": candidate.get("passReason"),
    }


def summarize_eval(path: Path) -> dict[str, Any]:
    data = read_json(path)
    sample_count = int(data.get("sampleCount") or 0)
    candidates = [candidate_summary(candidate, sample_count) for candidate in data.get("candidates", [])]
    wake_candidates = [candidate for candidate in candidates if candidate["wakePassed"]]
    command_candidates = [candidate for candidate in candidates if candidate["commandStable"]]
    current = next((candidate for candidate in candidates if candidate["id"] == CURRENT_CONFIG_CANDIDATE), None)
    return {
        "path": str(path),
        "status": data.get("status"),
        "sampleCount": sample_count,
        "selectedCandidate": data.get("selectedCandidate"),
        "candidates": candidates,
        "wakeGatePassed": bool(wake_candidates),
        "commandGatePassed": bool(command_candidates),
        "currentConfigWakePassed": bool(current and current["wakePassed"]),
        "currentConfigCommandPassed": bool(current and current["commandStable"]),
        "bestWakeCandidate": wake_candidates[0] if wake_candidates else None,
        "bestCommandCandidate": command_candidates[0] if command_candidates else None,
    }


def print_status(base_dir: Path) -> int:
    summary_path = base_dir / "summary.json"
    if summary_path.exists():
        data = read_json(summary_path)
        report = {
            "mode": "kiwi_two_step_stt_gate_status",
            "status": data.get("status"),
            "reason": data.get("reason"),
            "baseDir": str(base_dir),
            "liveReady": bool(data.get("liveReady")),
            "wake": {
                "wakeGatePassed": ((data.get("wake") or {}).get("wakeGatePassed")),
                "currentConfigWakePassed": ((data.get("wake") or {}).get("currentConfigWakePassed")),
                "bestWakeCandidate": ((data.get("wake") or {}).get("bestWakeCandidate")),
            },
            "command": {
                "commandGatePassed": ((data.get("command") or {}).get("commandGatePassed")),
                "currentConfigCommandPassed": ((data.get("command") or {}).get("currentConfigCommandPassed")),
                "bestCommandCandidate": ((data.get("command") or {}).get("bestCommandCandidate")),
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    report = {
        "timestamp": now_iso(),
        "mode": "kiwi_two_step_stt_gate_status",
        "status": "pending",
        "baseDir": str(base_dir),
        "summary": str(summary_path),
        "wakeManifestExists": (base_dir / "wake" / "manifest.json").exists(),
        "commandManifestExists": (base_dir / "command" / "manifest.json").exists(),
        "wakeEvalExists": (base_dir / "wake-eval.json").exists(),
        "commandEvalExists": (base_dir / "command-eval.json").exists(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v7.2.12 wake-only then command STT dry-run gate.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--wake-phrase", default=DEFAULT_WAKE_PHRASE)
    parser.add_argument("--command-phrase", default=DEFAULT_COMMAND_PHRASE)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--duration-ms", type=int, default=6000)
    parser.add_argument("--gap-ms", type=int, default=2500)
    parser.add_argument("--device-id", default="communications")
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    base_dir = args.base_dir.resolve()
    if args.status:
        return print_status(base_dir)
    if args.count <= 0:
        print("--count must be positive", file=sys.stderr)
        return 2
    if args.duration_ms <= 0:
        print("--duration-ms must be positive", file=sys.stderr)
        return 2
    if args.gap_ms < 0:
        print("--gap-ms must be zero or positive", file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0:
        print("--timeout-seconds must be positive", file=sys.stderr)
        return 2

    base_dir.mkdir(parents=True, exist_ok=True)
    wake_dir = base_dir / "wake"
    command_dir = base_dir / "command"
    wake_eval = base_dir / "wake-eval.json"
    command_eval = base_dir / "command-eval.json"
    summary_path = base_dir / "summary.json"

    report: dict[str, Any] = {
        "timestamp": now_iso(),
        "mode": "kiwi_two_step_stt_gate",
        "status": "blocked",
        "baseDir": str(base_dir),
        "wakePhrase": args.wake_phrase,
        "commandPhrase": args.command_phrase,
        "count": args.count,
        "durationMs": args.duration_ms,
        "gapMs": args.gap_ms,
        "deviceId": args.device_id,
        "commands": [],
        "wake": None,
        "command": None,
        "liveReady": False,
        "reason": None,
    }

    if not args.skip_capture:
        for phrase, out_dir in ((args.wake_phrase, wake_dir), (args.command_phrase, command_dir)):
            capture = run_command(
                [
                    "node",
                    "scripts/wsl/kiwi_browser_stt_capture_probe.mjs",
                    "--device-id",
                    args.device_id,
                    "--count",
                    str(args.count),
                    "--duration-ms",
                    str(args.duration_ms),
                    "--gap-ms",
                    str(args.gap_ms),
                    "--phrase",
                    phrase,
                    "--out-dir",
                    str(out_dir),
                ],
                timeout_seconds=max(90, int(args.count * (args.duration_ms + args.gap_ms) / 1000) + 60),
            )
            report["commands"].append(capture)
            if capture["returncode"] != 0:
                report["status"] = "failed"
                report["reason"] = f"capture failed for phrase: {phrase}"
                write_json(summary_path, report)
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
                return 1

    wake_result = run_command(
        [
            "python3",
            "scripts/wsl/kiwi_stt_eval_probe.py",
            "--samples-dir",
            str(wake_dir),
            "--out",
            str(wake_eval),
            "--candidate",
            "small_prompt_openclaw:small:오픈클로",
            "--timeout-seconds",
            str(args.timeout_seconds),
        ],
        timeout_seconds=args.timeout_seconds,
    )
    report["commands"].append(wake_result)
    if wake_result["returncode"] != 0:
        report["status"] = "failed"
        report["reason"] = "wake eval command failed"
        write_json(summary_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    command_result = run_command(
        [
            "python3",
            "scripts/wsl/kiwi_stt_eval_probe.py",
            "--samples-dir",
            str(command_dir),
            "--out",
            str(command_eval),
            "--allow-command-only",
            "--timeout-seconds",
            str(args.timeout_seconds),
        ],
        timeout_seconds=args.timeout_seconds,
    )
    report["commands"].append(command_result)
    if command_result["returncode"] != 0:
        report["status"] = "failed"
        report["reason"] = "command eval command failed"
        write_json(summary_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    report["wake"] = summarize_eval(wake_eval)
    report["command"] = summarize_eval(command_eval)
    report["liveReady"] = bool(
        report["wake"]["currentConfigWakePassed"] and report["command"]["currentConfigCommandPassed"]
    )
    if report["liveReady"]:
        report["status"] = "passed"
        report["reason"] = "current Kiwi STT config passed wake-only and command-only gates"
    elif report["wake"]["wakeGatePassed"] and report["command"]["commandGatePassed"]:
        report["status"] = "blocked"
        report["reason"] = "offline gates passed only with a non-current command candidate"
    elif not report["wake"]["wakeGatePassed"]:
        report["reason"] = "wake-only STT gate failed"
    else:
        report["reason"] = "command-only STT gate failed"

    write_json(summary_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
