# kiwi_stt_eval_probe.py - compare local faster-whisper candidates on captured Kiwi WAV samples.
# debug-autoloop: command=python3 scripts/wsl/kiwi_stt_eval_probe.py --status
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = ROOT / ".debugloop" / "artifacts" / "kiwi"
DEFAULT_KIWI_PATH = r"C:\Users\ksg63\projects\kiwi-voice"
DEFAULT_SAMPLES_DIR = ARTIFACT_DIR / "stt-samples-v7.2.11"
DEFAULT_OUT = ARTIFACT_DIR / "stt-eval-v7.2.11.json"
DEFAULT_WORKER = ARTIFACT_DIR / "kiwi-stt-eval-worker.py"


WORKER_SOURCE = r'''
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from faster_whisper import WhisperModel


WAKE_ALIASES = ("오픈클로", "오픈 클로", "오픈클로우", "오픈 클로우")
COMMAND_MARKERS = ("테스트", "알림", "보내")
HALLUCINATION_MARKERS = (
    "구독",
    "좋아요",
    "자막",
    "시청해주셔서",
    "시청해 주셔서",
    "감사합니다",
    "영상편집",
    "영상 편집",
    "편집자",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize(text: str) -> str:
    return re.sub(r"[\s,.;:!?，。！？·'\"`~\-_/\\()\[\]{}]+", "", text).lower()


def contains_wake(text: str) -> bool:
    normalized = normalize(text)
    return any(normalize(alias) in normalized for alias in WAKE_ALIASES)


def contains_command(text: str) -> bool:
    normalized = normalize(text)
    return all(marker in normalized for marker in COMMAND_MARKERS)


def contains_hallucination(text: str) -> bool:
    normalized = normalize(text)
    return any(normalize(marker) in normalized for marker in HALLUCINATION_MARKERS)


def parse_candidate(raw: str) -> dict:
    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"candidate must be id:model:prompt, got {raw!r}")
    candidate_id, model, prompt = parts
    if prompt == "__NONE__":
        prompt = ""
    return {"id": candidate_id, "model": model, "prompt": prompt}


def segment_record(segment) -> dict:
    return {
        "start": round(float(getattr(segment, "start", 0.0)), 3),
        "end": round(float(getattr(segment, "end", 0.0)), 3),
        "text": str(getattr(segment, "text", "")).strip(),
        "noSpeechProb": round(float(getattr(segment, "no_speech_prob", 0.0)), 4),
        "avgLogprob": round(float(getattr(segment, "avg_logprob", 0.0)), 4),
    }


def transcribe_sample(model: WhisperModel, sample: Path, args, prompt: str) -> dict:
    segments_iter, info = model.transcribe(
        str(sample),
        language=args.language,
        task="transcribe",
        beam_size=args.beam_size,
        best_of=args.best_of,
        temperature=args.temperature,
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
        no_speech_threshold=args.no_speech_threshold,
    )
    segments = [segment_record(segment) for segment in segments_iter]
    text = " ".join(segment["text"] for segment in segments).strip()
    return {
        "path": str(sample),
        "text": text,
        "segments": segments,
        "info": {
            "language": getattr(info, "language", None),
            "languageProbability": round(float(getattr(info, "language_probability", 0.0)), 4),
            "duration": round(float(getattr(info, "duration", 0.0)), 3),
        },
        "wakeDetected": contains_wake(text),
        "commandDetected": contains_command(text),
        "hallucinationDetected": contains_hallucination(text),
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Compare faster-whisper STT candidates on WAV samples.")
    parser.add_argument("--samples-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--language", default="ko")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--best-of", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-speech-threshold", type=float, default=0.85)
    parser.add_argument("--allow-command-only", action="store_true")
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    samples_dir = Path(args.samples_dir)
    samples = sorted(samples_dir.glob("*.wav"))
    candidates = [parse_candidate(raw) for raw in args.candidate]
    report = {
        "timestamp": now_iso(),
        "mode": "kiwi_stt_eval_probe",
        "status": "blocked",
        "samplesDir": str(samples_dir),
        "sampleCount": len(samples),
        "samples": [str(path) for path in samples],
        "language": args.language,
        "device": args.device,
        "computeType": args.compute_type,
        "allowCommandOnly": args.allow_command_only,
        "candidates": [],
        "selectedCandidate": None,
        "errors": [],
    }
    if not samples:
        report["errors"].append("no WAV samples found")
    if not candidates:
        report["errors"].append("no candidates configured")

    for candidate in candidates:
        candidate_report = {
            **candidate,
            "status": "blocked",
            "wakeHits": 0,
            "commandHits": 0,
            "hallucinationHits": 0,
            "results": [],
            "error": None,
        }
        try:
            model = WhisperModel(candidate["model"], device=args.device, compute_type=args.compute_type)
            for sample in samples:
                result = transcribe_sample(model, sample, args, candidate["prompt"])
                candidate_report["results"].append(result)
                candidate_report["wakeHits"] += 1 if result["wakeDetected"] else 0
                candidate_report["commandHits"] += 1 if result["commandDetected"] else 0
                candidate_report["hallucinationHits"] += 1 if result["hallucinationDetected"] else 0
            command_stable = candidate_report["commandHits"] >= max(2, len(samples) // 2 + 1)
            if candidate_report["wakeHits"] > 0:
                candidate_report["status"] = "passed"
                candidate_report["passReason"] = "wake phrase recognized"
            elif args.allow_command_only and command_stable:
                candidate_report["status"] = "passed"
                candidate_report["passReason"] = "stable command recognized for a future two-step wake flow"
            else:
                candidate_report["passReason"] = "wake phrase not recognized"
        except Exception as exc:
            candidate_report["status"] = "failed"
            candidate_report["error"] = str(exc)
        report["candidates"].append(candidate_report)

    for candidate in report["candidates"]:
        if candidate.get("status") == "passed":
            report["status"] = "passed"
            report["selectedCandidate"] = {
                "id": candidate["id"],
                "model": candidate["model"],
                "prompt": candidate["prompt"],
                "passReason": candidate.get("passReason"),
            }
            break

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


DEFAULT_CANDIDATES = (
    "small_prompt_openclaw:small:오픈클로",
    "small_no_prompt:small:__NONE__",
    "medium_prompt_openclaw:medium:오픈클로",
)


def ps_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def wsl_path_to_windows(path: Path) -> str:
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


def run_powershell(script: str, timeout: int) -> subprocess.CompletedProcess[str]:
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


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare faster-whisper STT candidates on captured Kiwi WAV samples.")
    parser.add_argument("--status", action="store_true", help="Print existing STT eval artifact status without transcribing.")
    parser.add_argument("--kiwi-path", default=DEFAULT_KIWI_PATH)
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--worker", type=Path, default=DEFAULT_WORKER)
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--language", default="ko")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--best-of", type=int, default=5)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-speech-threshold", type=float, default=0.85)
    parser.add_argument("--allow-command-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser.parse_args(argv)


def print_status(samples_dir: Path, out_file: Path) -> int:
    sample_paths = sorted(samples_dir.glob("*.wav"))
    report = {
        "mode": "kiwi_stt_eval_status",
        "status": "pending",
        "samplesDir": str(samples_dir),
        "out": str(out_file),
        "sampleCount": len(sample_paths),
        "samples": [str(path) for path in sample_paths],
        "evalStatus": None,
        "selectedCandidate": None,
        "error": None,
    }
    if out_file.exists():
        try:
            eval_data = json.loads(out_file.read_text(encoding="utf-8"))
            report["evalStatus"] = eval_data.get("status")
            report["selectedCandidate"] = eval_data.get("selectedCandidate")
            report["status"] = str(eval_data.get("status") or "pending")
        except (json.JSONDecodeError, OSError) as exc:
            report["status"] = "warning"
            report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    samples_dir = args.samples_dir.resolve()
    out_file = args.out.resolve()
    if args.status:
        return print_status(samples_dir, out_file)
    if args.beam_size <= 0:
        print("--beam-size must be positive", file=sys.stderr)
        return 2
    if args.best_of <= 0:
        print("--best-of must be positive", file=sys.stderr)
        return 2
    if args.temperature < 0:
        print("--temperature must be zero or positive", file=sys.stderr)
        return 2
    if not 0 <= args.no_speech_threshold <= 1:
        print("--no-speech-threshold must be between 0 and 1", file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0:
        print("--timeout-seconds must be positive", file=sys.stderr)
        return 2

    worker_file = args.worker.resolve()
    worker_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    worker_file.write_text(WORKER_SOURCE.lstrip(), encoding="utf-8")

    candidates = args.candidate or list(DEFAULT_CANDIDATES)
    python_path = args.kiwi_path + r"\venv\Scripts\python.exe"
    worker_path = wsl_path_to_windows(worker_file)
    samples_path = wsl_path_to_windows(samples_dir)
    out_path = wsl_path_to_windows(out_file)
    command = (
        "$python=" + ps_literal(python_path) + "; "
        "$worker=" + ps_literal(worker_path) + "; "
        "$samples=" + ps_literal(samples_path) + "; "
        "$out=" + ps_literal(out_path) + "; "
        "if (-not (Test-Path -LiteralPath $python)) { throw \"Kiwi venv python not found: $python\" }; "
        "& $python $worker "
        "--samples-dir $samples "
        "--out $out "
        "--language " + ps_literal(args.language) + " "
        "--device " + ps_literal(args.device) + " "
        "--compute-type " + ps_literal(args.compute_type) + " "
        f"--beam-size {args.beam_size} "
        f"--best-of {args.best_of} "
        f"--temperature {args.temperature} "
        f"--no-speech-threshold {args.no_speech_threshold} "
    )
    if args.allow_command_only:
        command += "--allow-command-only "
    for candidate in candidates:
        command += "--candidate " + ps_literal(candidate) + " "

    completed = run_powershell(command, timeout=max(60, args.timeout_seconds))
    print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
