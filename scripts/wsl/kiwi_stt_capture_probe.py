# kiwi_stt_capture_probe.py - capture Windows microphone WAV samples for Kiwi STT comparison.
# debug-autoloop: command=python3 scripts/wsl/kiwi_stt_capture_probe.py --status
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
DEFAULT_OUT_DIR = ARTIFACT_DIR / "stt-samples-v7.2.11"
DEFAULT_WORKER = ARTIFACT_DIR / "kiwi-stt-capture-worker.py"
DEFAULT_PHRASE = "오픈클로, 테스트 알림 보내줘"


WORKER_SOURCE = r'''
from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def input_devices() -> list[dict]:
    devices = []
    defaults = sd.default.device
    default_input = defaults[0] if isinstance(defaults, (list, tuple)) else defaults
    for index, device in enumerate(sd.query_devices()):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        devices.append(
            {
                "index": index,
                "name": str(device.get("name", "")),
                "maxInputChannels": int(device.get("max_input_channels", 0)),
                "defaultSampleRate": float(device.get("default_samplerate", 0)),
                "isDefaultInput": index == default_input,
            }
        )
    return devices


def choose_device(devices: list[dict], label: str | None, explicit_index: int | None) -> int | None:
    if explicit_index is not None:
        return explicit_index
    if label:
        needle = label.lower()
        for device in devices:
            if needle in device["name"].lower():
                return int(device["index"])
    for device in devices:
        if device.get("isDefaultInput"):
            return int(device["index"])
    return int(devices[0]["index"]) if devices else None


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(audio.reshape(-1), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def measure(audio: np.ndarray) -> dict:
    flat = audio.reshape(-1).astype(np.float32)
    if flat.size == 0:
        return {"rms": 0.0, "peak": 0.0, "meanAbs": 0.0, "samples": 0}
    return {
        "rms": round(float(np.sqrt(np.mean(np.square(flat)))), 6),
        "peak": round(float(np.max(np.abs(flat))), 6),
        "meanAbs": round(float(np.mean(np.abs(flat))), 6),
        "samples": int(flat.size),
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Capture Windows microphone WAV samples for STT comparison.")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--duration-ms", type=int, default=6000)
    parser.add_argument("--gap-ms", type=int, default=2500)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--device-label", default="")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--min-rms", type=float, default=0.015)
    parser.add_argument("--phrase", default="오픈클로, 테스트 알림 보내줘")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--manifest", required=True)
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    devices = input_devices()
    device_index = choose_device(devices, args.device_label, args.device_index)
    report = {
        "timestamp": now_iso(),
        "mode": "kiwi_stt_capture_probe",
        "status": "blocked",
        "phrase": args.phrase,
        "count": args.count,
        "durationMs": args.duration_ms,
        "gapMs": args.gap_ms,
        "sampleRate": args.sample_rate,
        "channels": args.channels,
        "minRms": args.min_rms,
        "deviceLabel": args.device_label,
        "deviceIndex": device_index,
        "devices": devices,
        "samples": [],
        "error": None,
    }
    try:
        if device_index is None:
            raise RuntimeError("no Windows input devices found")
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = max(1, int(args.sample_rate * args.duration_ms / 1000))
        for index in range(1, args.count + 1):
            if index > 1 and args.gap_ms > 0:
                time.sleep(args.gap_ms / 1000)
            audio = sd.rec(
                frames,
                samplerate=args.sample_rate,
                channels=args.channels,
                dtype="float32",
                device=device_index,
            )
            sd.wait()
            audio = np.asarray(audio, dtype=np.float32)
            if audio.ndim > 1:
                audio = np.mean(audio, axis=1, keepdims=True)
            sample_path = out_dir / f"sample-{index:02d}.wav"
            write_wav(sample_path, audio, args.sample_rate)
            stats = measure(audio)
            report["samples"].append(
                {
                    "index": index,
                    "path": str(sample_path),
                    "measurement": stats,
                    "passedRmsGate": stats["rms"] >= args.min_rms,
                }
            )
        report["status"] = "passed" if any(item["passedRmsGate"] for item in report["samples"]) else "blocked"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)

    manifest = Path(args.manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


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
    parser = argparse.ArgumentParser(description="Capture Windows microphone WAV samples for Kiwi STT comparison.")
    parser.add_argument("--status", action="store_true", help="Print existing capture manifest status without recording.")
    parser.add_argument("--kiwi-path", default=DEFAULT_KIWI_PATH)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--duration-ms", type=int, default=6000)
    parser.add_argument("--gap-ms", type=int, default=2500)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--device-label", default="")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--min-rms", type=float, default=0.015)
    parser.add_argument("--phrase", default=DEFAULT_PHRASE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--worker", type=Path, default=DEFAULT_WORKER)
    return parser.parse_args(argv)


def print_status(out_dir: Path, manifest: Path) -> int:
    sample_paths = sorted(out_dir.glob("*.wav"))
    report = {
        "mode": "kiwi_stt_capture_status",
        "status": "pending",
        "outDir": str(out_dir),
        "manifest": str(manifest),
        "sampleCount": len(sample_paths),
        "samples": [str(path) for path in sample_paths],
        "manifestStatus": None,
        "error": None,
    }
    if manifest.exists():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            report["manifestStatus"] = manifest_data.get("status")
            report["phrase"] = manifest_data.get("phrase")
            report["status"] = "ok" if sample_paths else str(manifest_data.get("status") or "pending")
        except (json.JSONDecodeError, OSError) as exc:
            report["status"] = "warning"
            report["error"] = str(exc)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    out_dir = args.out_dir.resolve()
    manifest = (args.manifest or out_dir / "manifest.json").resolve()
    if args.status:
        return print_status(out_dir, manifest)
    if args.count <= 0:
        print("--count must be positive", file=sys.stderr)
        return 2
    if args.duration_ms <= 0:
        print("--duration-ms must be positive", file=sys.stderr)
        return 2
    if args.gap_ms < 0:
        print("--gap-ms must be zero or positive", file=sys.stderr)
        return 2
    if args.sample_rate <= 0:
        print("--sample-rate must be positive", file=sys.stderr)
        return 2
    if args.channels <= 0:
        print("--channels must be positive", file=sys.stderr)
        return 2
    if args.min_rms < 0:
        print("--min-rms must be zero or positive", file=sys.stderr)
        return 2

    worker_file = args.worker.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    worker_file.parent.mkdir(parents=True, exist_ok=True)
    worker_file.write_text(WORKER_SOURCE.lstrip(), encoding="utf-8")

    python_path = args.kiwi_path + r"\venv\Scripts\python.exe"
    worker_path = wsl_path_to_windows(worker_file)
    out_dir_path = wsl_path_to_windows(out_dir)
    manifest_path = wsl_path_to_windows(manifest)
    command = (
        "$python=" + ps_literal(python_path) + "; "
        "$worker=" + ps_literal(worker_path) + "; "
        "$outDir=" + ps_literal(out_dir_path) + "; "
        "$manifest=" + ps_literal(manifest_path) + "; "
        "if (-not (Test-Path -LiteralPath $python)) { throw \"Kiwi venv python not found: $python\" }; "
        "& $python $worker "
        f"--count {args.count} "
        f"--duration-ms {args.duration_ms} "
        f"--gap-ms {args.gap_ms} "
        f"--sample-rate {args.sample_rate} "
        f"--channels {args.channels} "
        f"--min-rms {args.min_rms} "
        "--phrase " + ps_literal(args.phrase) + " "
    )
    if args.device_label:
        command += "--device-label " + ps_literal(args.device_label) + " "
    if args.device_index is not None:
        command += f"--device-index {args.device_index} "
    command += "--out-dir $outDir --manifest $manifest"

    timeout_seconds = max(60, int(args.count * (args.duration_ms + args.gap_ms) / 1000) + 45)
    completed = run_powershell(command, timeout=timeout_seconds)
    print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
