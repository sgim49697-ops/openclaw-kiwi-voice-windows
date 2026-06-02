# Kiwi Voice Windows v7.1 Setup Plan

이 문서는 Windows Kiwi Voice 설치와 repo-local dry-run bridge 연결을 위한 v7.1 기준 절차다.

## Current Status

```text
Gateway: running, loopback-only 127.0.0.1:18789
Gateway approvals: allowlist + ask=always + askFallback=deny + autoAllowSkills=off
Windows Node: paired + connected
Windows tools: uv, python 3.12, git, FFmpeg, OpenClaw CLI available
Kiwi path: C:\Users\ksg63\projects\kiwi-voice cloned
Kiwi venv: created with uv
Dashboard: http://127.0.0.1:7789 reachable
Blocker: none for v7.1; v7.2 still needs live microphone/owner voice registration
```

v7.1은 Kiwi 설치와 startup 준비까지만 다룬다. Kiwi에서 실제 dispatcher 실행, `system.run`, browser action, Codex execution은 하지 않는다.

## Official Baseline

- Kiwi installation requires Python 3.10+, FFmpeg, and local OpenClaw.
- Kiwi uses `config.yaml` for language, STT, TTS, wake word, security, and API settings.
- Secrets and provider overrides belong in `.env`, not this repo.
- First run starts with `python -m kiwi`; the dashboard is `http://localhost:7789`.

References:

- https://docs.kiwi-voice.com/getting-started/installation/
- https://docs.kiwi-voice.com/getting-started/configuration/
- https://docs.kiwi-voice.com/getting-started/first-run/

## Windows Install Gate

Run the repo probe first:

```bash
python3 scripts/wsl/kiwi_windows_probe.py
```

If `ffmpeg.exe` is missing, install FFmpeg on Windows and confirm:

```powershell
ffmpeg -version
```

Do not proceed to Kiwi install until FFmpeg is on PATH.

In this WSL-launched Windows shell, a newly installed winget binary may require refreshing Machine/User PATH. The repo probe does that automatically before checking commands.

## Install Commands

Use PowerShell in Windows:

```powershell
mkdir $env:USERPROFILE\projects -Force
git clone https://github.com/ekleziast/kiwi-voice.git $env:USERPROFILE\projects\kiwi-voice
cd $env:USERPROFILE\projects\kiwi-voice

uv venv venv
.\venv\Scripts\activate
where.exe python
uv pip install -r requirements.txt
copy .env.example .env
```

Install PyTorch with the CUDA 12.8 index before the general requirements so it does not fall back to a CPU wheel:

```powershell
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
uv pip install -r requirements.txt
```

Copy `templates/kiwi/config.yaml.template` into the Kiwi repo as `config.yaml`, then adjust only local device fields if needed.

Current OpenClaw Gateway uses protocol v4 while this Kiwi checkout still has a Gateway v3 WebSocket client. For v7.1, `websocket.enabled=false` and `KIWI_WS_ENABLED=false` are used so Kiwi starts through the Windows OpenClaw CLI fallback. Direct dispatcher execution remains out of scope.

## Dry-run Bridge

Before using a microphone, test the transcript bridge:

```bash
python3 scripts/wsl/kiwi_transcript_dry_run.py --transcript "오픈클로, 테스트 알림 보내줘"
python3 scripts/wsl/kiwi_transcript_dry_run.py --transcript "오픈클로, Codex로 현재 프로젝트 다음 계획 세워줘"
python3 scripts/wsl/kiwi_transcript_dry_run.py --transcript "취소"
python3 scripts/wsl/kiwi_transcript_dry_run.py --transcript "오픈클로, 결제하고 Gmail로 비밀번호 보내"
```

Expected:

```text
notify/Codex: approvalRequest only, wouldExecute=false
cancel: no approvalRequest
critical request: deny, no approvalRequest
```

## v7.2 Handoff

After v7.1 succeeds:

```powershell
python -m kiwi
```

Then use the dashboard at `http://localhost:7789` for microphone, wake word, and owner voice registration. Telegram approval remains unset until a later explicit secrets batch.

Known v7.2 notes:

- Silero VAD may prompt for trusting `snakers4_silero-vad`; current run fell back to energy-only VAD.
- `pyannote/embedding` is gated on Hugging Face, so speaker ID owner registration needs a local model or approved token later.
