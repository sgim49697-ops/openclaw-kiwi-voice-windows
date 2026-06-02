# Kiwi Voice Windows v7.1 Setup Plan

이 문서는 Windows Kiwi Voice 설치와 repo-local dry-run bridge 연결을 위한 v7.1 기준 절차다.

## Current Status

```text
Gateway: running, loopback-only 127.0.0.1:18789
Gateway approvals: allowlist + ask=always + askFallback=deny + autoAllowSkills=off
Windows Node: paired + connected
Windows tools: uv, python 3.12, git available
Kiwi path: C:\Users\ksg63\projects\kiwi-voice not cloned yet
Blocker: ffmpeg.exe is not available on Windows PATH
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

If PyTorch must be installed manually, use the CUDA 12.8 index:

```powershell
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

Copy `templates/kiwi/config.yaml.template` into the Kiwi repo as `config.yaml`, then adjust only local device fields if needed.

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
