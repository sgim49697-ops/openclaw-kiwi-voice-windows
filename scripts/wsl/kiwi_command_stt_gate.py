# kiwi_command_stt_gate.py - run the v7.2.14 command-only STT dry-run gate.
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
DEFAULT_BASE_DIR = ARTIFACT_DIR / "command-stt-v7.2.14"
DEFAULT_PHRASE = "테스트 알림 보내줘"
SMALL_CANDIDATES = (
    "small_dialog_prompt_commands:small:테스트 알림 보내줘. 취소. 결제. Gmail. 비밀번호.",
    "small_notify_prompt:small:테스트 알림 보내줘",
    "small_short_prompt:small:테스트 알림. 알림 보내줘. 취소.",
)
MEDIUM_CANDIDATES = (
    "medium_notify_prompt:medium:테스트 알림 보내줘",
)
OUTPUT_LIMIT = 4000


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def trim_output(value: str) -> str:
    if len(value) <= OUTPUT_LIMIT:
        return value
    return value[:OUTPUT_LIMIT] + f"\n... <truncated {len(value) - OUTPUT_LIMIT} chars; see JSON artifacts> ..."


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


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_command(
    *,
    out_dir: Path,
    phrase: str,
    count: int,
    duration_ms: int,
    gap_ms: int,
    device_id: str,
    raw_audio: bool,
    timeout_ms: int,
) -> dict[str, Any]:
    command = [
        "node",
        "scripts/wsl/kiwi_browser_stt_capture_probe.mjs",
        "--device-id",
        device_id,
        "--count",
        str(count),
        "--duration-ms",
        str(duration_ms),
        "--gap-ms",
        str(gap_ms),
        "--timeout-ms",
        str(timeout_ms),
        "--phrase",
        phrase,
        "--out-dir",
        str(out_dir),
    ]
    if raw_audio:
        command.append("--raw-audio")
    timeout_seconds = max(120, int(count * (duration_ms + gap_ms) / 1000) + 90)
    return run_command(command, timeout_seconds=timeout_seconds)


def eval_command(samples_dir: Path, out_file: Path, candidates: Sequence[str], timeout_seconds: int) -> dict[str, Any]:
    command = [
        "python3",
        "scripts/wsl/kiwi_stt_eval_probe.py",
        "--samples-dir",
        str(samples_dir),
        "--out",
        str(out_file),
        "--allow-command-only",
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    for candidate in candidates:
        command.extend(["--candidate", candidate])
    return run_command(command, timeout_seconds=timeout_seconds)


def sample_status(sample_dir: Path) -> dict[str, Any]:
    manifest_path = sample_dir / "manifest.json"
    samples = sorted(sample_dir.glob("*.wav"))
    report: dict[str, Any] = {
        "dir": str(sample_dir),
        "manifest": str(manifest_path),
        "sampleCount": len(samples),
        "samples": [str(path) for path in samples],
        "manifestStatus": None,
        "rmsPassedCount": 0,
        "maxRms": None,
    }
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        report["manifestStatus"] = manifest.get("status")
        rms_values = [
            float(((sample.get("measurement") or {}).get("rms")) or 0)
            for sample in manifest.get("samples", [])
        ]
        report["maxRms"] = max(rms_values) if rms_values else None
        report["rmsPassedCount"] = sum(1 for sample in manifest.get("samples", []) if sample.get("passedRmsGate"))
    return report


def conservative_candidate_summary(candidate: dict[str, Any], sample_count: int, threshold: int) -> dict[str, Any]:
    command_hits = int(candidate.get("commandHits") or 0)
    constrained_hits = int(candidate.get("constrainedCommandHits") or 0)
    hallucination_hits = int(candidate.get("hallucinationHits") or 0)
    critical_hits = int(candidate.get("criticalDenyHits") or 0)
    pass_by_command = command_hits >= threshold
    pass_by_constrained = constrained_hits >= threshold
    passed = (pass_by_command or pass_by_constrained) and hallucination_hits < 2 and critical_hits == 0
    if critical_hits:
        reason = "critical marker detected"
    elif hallucination_hits >= 2:
        reason = "hallucination marker threshold exceeded"
    elif pass_by_command:
        reason = "raw command hit threshold passed"
    elif pass_by_constrained:
        reason = "constrained dry-run route threshold passed"
    else:
        reason = "command threshold not met"
    return {
        "id": candidate.get("id"),
        "model": candidate.get("model"),
        "prompt": candidate.get("prompt"),
        "sampleCount": sample_count,
        "commandHits": command_hits,
        "constrainedCommandHits": constrained_hits,
        "hallucinationHits": hallucination_hits,
        "criticalDenyHits": critical_hits,
        "passed": passed,
        "reason": reason,
    }


def summarize_eval(path: Path, threshold: int) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "status": "missing", "sampleCount": 0, "candidates": [], "winner": None}
    data = read_json(path)
    sample_count = int(data.get("sampleCount") or 0)
    candidates = [
        conservative_candidate_summary(candidate, sample_count, threshold)
        for candidate in data.get("candidates", [])
    ]
    winners = [candidate for candidate in candidates if candidate["passed"]]
    return {
        "path": str(path),
        "status": "passed" if winners else "blocked",
        "sampleCount": sample_count,
        "threshold": threshold,
        "candidates": candidates,
        "winner": winners[0] if winners else None,
    }


def print_status(base_dir: Path) -> int:
    summary_path = base_dir / "summary.json"
    if summary_path.exists():
        data = read_json(summary_path)
        report = {
            "mode": "kiwi_command_stt_gate_status",
            "status": data.get("status"),
            "reason": data.get("reason"),
            "baseDir": str(base_dir),
            "liveReady": bool(data.get("liveReady")),
            "selectedPrompt": ((data.get("selectedCandidate") or {}).get("prompt")),
            "standard": {
                "sample": ((data.get("standard") or {}).get("sample")),
                "small": ((data.get("standard") or {}).get("small")),
                "medium": ((data.get("standard") or {}).get("medium")),
            },
            "raw": {
                "sample": ((data.get("raw") or {}).get("sample")),
                "small": ((data.get("raw") or {}).get("small")),
                "medium": ((data.get("raw") or {}).get("medium")),
            },
        }
    else:
        report = {
            "timestamp": now_iso(),
            "mode": "kiwi_command_stt_gate_status",
            "status": "pending",
            "baseDir": str(base_dir),
            "summary": str(summary_path),
            "standardSamples": sample_status(base_dir / "standard"),
            "rawSamples": sample_status(base_dir / "raw"),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the v7.2.14 command-only STT dry-run gate.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--skip-medium", action="store_true")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--duration-ms", type=int, default=6000)
    parser.add_argument("--gap-ms", type=int, default=2500)
    parser.add_argument("--device-id", default="communications")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.count <= 0:
        raise ValueError("--count must be positive")
    if args.duration_ms <= 0:
        raise ValueError("--duration-ms must be positive")
    if args.gap_ms < 0:
        raise ValueError("--gap-ms must be zero or positive")
    if args.timeout_ms <= 0:
        raise ValueError("--timeout-ms must be positive")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")


def run_eval_group(name: str, sample_dir: Path, base_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    small_eval = base_dir / f"{name}-small-eval.json"
    medium_eval = base_dir / f"{name}-medium-eval.json"
    commands: list[dict[str, Any]] = []
    samples = sample_status(sample_dir)
    if samples["sampleCount"] == 0:
        return {
            "sample": samples,
            "small": {"path": str(small_eval), "status": "missing_samples", "sampleCount": 0, "candidates": [], "winner": None},
            "medium": {"path": str(medium_eval), "status": "skipped", "sampleCount": 0, "candidates": [], "winner": None},
            "winner": None,
            "commands": commands,
        }

    small_result = eval_command(sample_dir, small_eval, SMALL_CANDIDATES, args.timeout_seconds)
    commands.append(small_result)
    small_summary = summarize_eval(small_eval, threshold=max(3, args.count // 2 + 1))

    medium_summary = {"path": str(medium_eval), "status": "skipped", "sampleCount": 0, "candidates": [], "winner": None}
    if not args.skip_medium and not small_summary.get("winner"):
        medium_result = eval_command(sample_dir, medium_eval, MEDIUM_CANDIDATES, args.timeout_seconds)
        commands.append(medium_result)
        medium_summary = summarize_eval(medium_eval, threshold=max(3, args.count // 2 + 1))

    winner = small_summary.get("winner") or medium_summary.get("winner")
    return {
        "sample": samples,
        "small": small_summary,
        "medium": medium_summary,
        "winner": winner,
        "commands": commands,
    }


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    base_dir = args.base_dir.resolve()
    if args.status:
        return print_status(base_dir)
    try:
        validate_args(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    base_dir.mkdir(parents=True, exist_ok=True)
    standard_dir = base_dir / "standard"
    raw_dir = base_dir / "raw"
    summary_path = base_dir / "summary.json"
    report: dict[str, Any] = {
        "timestamp": now_iso(),
        "mode": "kiwi_command_stt_gate",
        "status": "blocked",
        "reason": None,
        "baseDir": str(base_dir),
        "phrase": args.phrase,
        "count": args.count,
        "durationMs": args.duration_ms,
        "gapMs": args.gap_ms,
        "deviceId": args.device_id,
        "threshold": max(3, args.count // 2 + 1),
        "commands": [],
        "standard": None,
        "raw": None,
        "selectedCandidate": None,
        "liveReady": False,
    }

    if not args.skip_capture:
        for name, out_dir, raw_audio in (("standard", standard_dir, False), ("raw", raw_dir, True)):
            capture = capture_command(
                out_dir=out_dir,
                phrase=args.phrase,
                count=args.count,
                duration_ms=args.duration_ms,
                gap_ms=args.gap_ms,
                device_id=args.device_id,
                raw_audio=raw_audio,
                timeout_ms=args.timeout_ms,
            )
            report["commands"].append(capture)
            if capture["returncode"] != 0:
                report["status"] = "failed"
                report["reason"] = f"{name} capture failed"
                write_json(summary_path, report)
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
                return 1

    report["standard"] = run_eval_group("standard", standard_dir, base_dir, args)
    report["commands"].extend(report["standard"]["commands"])
    report["raw"] = run_eval_group("raw", raw_dir, base_dir, args)
    report["commands"].extend(report["raw"]["commands"])

    selected = report["standard"].get("winner") or report["raw"].get("winner")
    if selected:
        report["status"] = "passed"
        report["reason"] = "offline command STT gate passed"
        report["selectedCandidate"] = selected
        report["liveReady"] = True
    else:
        report["reason"] = "fresh command STT gate failed"

    write_json(summary_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
