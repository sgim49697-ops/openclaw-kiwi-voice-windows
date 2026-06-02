# kiwi_windows_audio_probe.py - measure Windows native microphone RMS through the Kiwi venv.
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
DEFAULT_OUT = ARTIFACT_DIR / "windows-audio-probe.json"
DEFAULT_WORKER = ARTIFACT_DIR / "windows-audio-capture-worker.py"


WORKER_SOURCE = r'''
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_name(device) -> str:
    return str(device.get("name", ""))


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
                "name": safe_name(device),
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


def measure_device(device_index: int, args) -> dict:
    record = {
        "deviceIndex": device_index,
        "durationMs": args.duration_ms,
        "sampleRate": args.sample_rate,
        "channels": args.channels,
        "measurement": None,
        "status": "blocked",
        "error": None,
    }
    try:
        frames = max(1, int(args.sample_rate * args.duration_ms / 1000))
        audio = sd.rec(frames, samplerate=args.sample_rate, channels=args.channels, dtype="float32", device=device_index)
        sd.wait()
        audio = np.asarray(audio, dtype=np.float32)
        rms = float(np.sqrt(np.mean(np.square(audio))))
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        record["measurement"] = {
            "rms": round(rms, 6),
            "peak": round(peak, 6),
            "meanAbs": round(float(np.mean(np.abs(audio))), 6),
            "shape": list(audio.shape),
            "samples": int(audio.shape[0]) if audio.ndim else int(audio.size),
        }
        record["status"] = "ok"
    except Exception as exc:
        record["error"] = str(exc)
    return record


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Measure Windows native microphone level.")
    parser.add_argument("--duration-ms", type=int, default=5000)
    parser.add_argument("--per-device-ms", type=int, default=3000)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--device-label", default="USB Audio Device")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--scan-all", action="store_true")
    parser.add_argument("--min-rms", type=float, default=0.015)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    devices = input_devices()
    device_index = choose_device(devices, args.device_label, args.device_index)
    record = {
        "timestamp": now_iso(),
        "mode": "kiwi_windows_audio_scan" if args.scan_all else "kiwi_windows_audio_probe",
        "status": "blocked",
        "durationMs": args.duration_ms,
        "perDeviceMs": args.per_device_ms,
        "sampleRate": args.sample_rate,
        "channels": args.channels,
        "deviceLabel": args.device_label,
        "deviceIndex": device_index,
        "minRms": args.min_rms,
        "scanAll": args.scan_all,
        "devices": devices,
        "measurement": None,
        "rankings": [],
        "bestDevice": None,
        "error": None,
    }
    try:
        if device_index is None:
            raise RuntimeError("no input devices found")
        if args.scan_all:
            scan_args = argparse.Namespace(
                duration_ms=args.per_device_ms,
                sample_rate=args.sample_rate,
                channels=args.channels,
            )
            for device in devices:
                measurement = measure_device(int(device["index"]), scan_args)
                measurement["device"] = device
                record["rankings"].append(measurement)
            record["rankings"].sort(
                key=lambda item: (item.get("measurement") or {}).get("rms", -1),
                reverse=True,
            )
            record["bestDevice"] = record["rankings"][0] if record["rankings"] else None
            best_rms = ((record["bestDevice"] or {}).get("measurement") or {}).get("rms", 0)
            record["status"] = "passed" if best_rms >= args.min_rms else "blocked"
        else:
            measurement = measure_device(device_index, args)
            record["measurement"] = measurement.get("measurement")
            record["error"] = measurement.get("error")
            record["status"] = measurement.get("status", "blocked")
    except Exception as exc:
        record["error"] = str(exc)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if record["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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
    parser = argparse.ArgumentParser(description="Measure Windows native microphone RMS through the Kiwi venv.")
    parser.add_argument("--kiwi-path", default=DEFAULT_KIWI_PATH)
    parser.add_argument("--duration-ms", type=int, default=5000)
    parser.add_argument("--per-device-ms", type=int, default=3000)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--device-label", default="USB Audio Device")
    parser.add_argument("--device-index", type=int)
    parser.add_argument("--scan-all", action="store_true")
    parser.add_argument("--min-rms", type=float, default=0.015)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--worker", type=Path, default=DEFAULT_WORKER)
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.duration_ms <= 0:
        print("--duration-ms must be positive", file=sys.stderr)
        return 2
    if args.per_device_ms <= 0:
        print("--per-device-ms must be positive", file=sys.stderr)
        return 2
    if args.sample_rate <= 0:
        print("--sample-rate must be positive", file=sys.stderr)
        return 2
    if args.channels <= 0:
        print("--channels must be positive", file=sys.stderr)
        return 2

    worker_file = args.worker.resolve()
    out_file = args.out.resolve()
    worker_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    worker_file.write_text(WORKER_SOURCE.lstrip(), encoding="utf-8")

    python_path = args.kiwi_path + r"\venv\Scripts\python.exe"
    worker_path = wsl_path_to_windows(worker_file)
    out_path = wsl_path_to_windows(out_file)
    command = (
        "$python=" + ps_literal(python_path) + "; "
        "$worker=" + ps_literal(worker_path) + "; "
        "$out=" + ps_literal(out_path) + "; "
        "if (-not (Test-Path -LiteralPath $python)) { throw \"Kiwi venv python not found: $python\" }; "
        "& $python $worker "
        f"--duration-ms {args.duration_ms} "
        f"--per-device-ms {args.per_device_ms} "
        f"--sample-rate {args.sample_rate} "
        f"--channels {args.channels} "
        f"--min-rms {args.min_rms} "
        "--device-label " + ps_literal(args.device_label) + " "
    )
    if args.device_index is not None:
        command += f"--device-index {args.device_index} "
    if args.scan_all:
        command += "--scan-all "
    command += "--out $out"

    timeout_seconds = max(20, int(args.duration_ms / 1000) + 20)
    if args.scan_all:
        # The exact input count is discovered inside the Windows worker, so use
        # a conservative host timeout for the largest observed local device set.
        timeout_seconds = max(90, int(args.per_device_ms / 1000) * 16 + 30)
    completed = run_powershell(command, timeout=timeout_seconds)
    print(completed.stdout.strip())
    if completed.stderr.strip():
        print(completed.stderr.strip(), file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
