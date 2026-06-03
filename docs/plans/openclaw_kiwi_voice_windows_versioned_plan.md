# OpenClaw Kiwi Voice Windows 버전별 진행 계획

**작성일:** 2026-06-01
**기준 문서:** `docs/plans/openclaw_kiwi_voice_windows_plan.md`
**목표:** Windows PC를 음성 호출 기반 OpenClaw 실행 환경으로 만들되, Gateway, Windows Node, 브라우저, Codex, Kiwi Voice를 단계별로 검증하며 안전하게 확장한다.

## 진행 원칙

- WSL2 Gateway를 중심에 두고 Windows Node와 Kiwi Voice는 점진적으로 붙인다.
- 음성 UX보다 권한 경계 검증을 먼저 끝낸다.
- `system.run`은 wrapper script만 허용한다.
- 브라우저 자동화는 `browse-read`, `browse-interact`, `browser-high-impact`로 분리한다.
- Codex는 기본적으로 read-only plan을 먼저 실행하고, 파일 수정은 별도 승인 후 진행한다.
- 각 버전은 산출물, 검증, 다음 단계 진입 조건이 모두 충족되어야 완료로 본다.

## 버전 요약

| 버전 | 목표 | 핵심 산출물 | 다음 단계 진입 조건 |
|---|---|---|---|
| v0 | 문서와 기준선 정리 | `docs/plans` 정리, 버전 계획 | 계획 파일 위치와 진행 순서 확정 |
| v1 | WSL2 + Gateway 기반 구축 | Ubuntu 24.04, OpenClaw Gateway | `openclaw doctor`, `gateway status` 통과 |
| v2 | Windows Node 최소 연결 | Node pairing, 알림/상태 확인 | `device.status`, `system.notify` 성공 |
| v3 | 실행 권한 경계 잠금 | central dispatcher-only `system.run`, exec approvals | 임의 명령 차단, dispatcher action만 승인 후 실행 |
| v4 | 브라우저 read 자동화 | 격리 `openclaw` 프로필, snapshot/screenshot | 안전 페이지 읽기와 스크린샷 성공 |
| v5 | 브라우저 interact 자동화 | click/type/fill/select 정책 | 테스트 페이지에서 클릭/입력 성공, side-effect 차단 |
| v6 | VS Code + Codex plan 연동 | `open_vscode_codex_plan` dispatcher action | read-only `/plan` 실행 성공 |
| v7 | Kiwi Voice 통합 | voice dry-run 계약, Windows native Kiwi, Codex OAuth planner, speaker ID, Telegram approval | 음성 호출이 Codex planner 승인 흐름까지 도달 |
| v8 | 운영 안정화 | 로그/백업/롤백/정책 문서화 | end-to-end smoke test와 롤백 절차 확인 |

## 현재 진행 상태 - 2026-06-03

v1 Gateway, v2 Windows Node 최소 연결, v3 dispatcher-only Windows 실행 경계는 통과 상태로 본다.

```text
Gateway:
  - WSL2 user service enabled/running
  - gateway reachable
  - exec approvals locked
  - security=allowlist, ask=always, askFallback=deny, autoAllowSkills=off

Windows Node:
  - Known: 1
  - Paired: 1
  - Connected: 1
  - safe smoke test: device.status, system.notify, screen.snapshot 성공
  - pending request: []

Windows dispatcher:
  - C:\OpenClawActions\Invoke-OpenClawAction.ps1 배포 완료
  - Windows Node local exec-policy dispatcher-only + default deny 적용
  - dispatcher notify direct smoke 성공
  - payloadHash mismatch 거부 성공

Browser:
  - local openclaw profile reset 직후 raw CDP Runtime/Page/Accessibility 일시 성공
  - local openclaw profile 재시작 후 rendererCount 0 / raw CDP page command timeout 재발
  - local openclaw profile의 OpenClaw wrapper doctor/snapshot/screenshot은 계속 timeout
  - Windows dedicated Chrome CDP profile windows-cdp 추가
  - windows-cdp profile open/doctor/snapshot/screenshot/console/errors 성공
  - windows-cdp profile click/type/fill/select interact smoke 성공
  - browser.defaultProfile: windows-cdp
  - browser.ssrfPolicy.allowedHostnames: 127.0.0.1, localhost

Codex plan:
  - 현재 repo는 WSL UNC 경로에 있으며 Windows Git은 UNC repo를 dubious ownership으로 거부
  - v6 route는 Windows Codex UNC 직접 실행이 아니라 VS Code WSL remote + WSL Codex read-only plan
  - 허용 project root: \\wsl.localhost\Ubuntu-22.04\home\user\projects\openclaw-kiwi-voice-windows
  - C:\OpenClawActions dispatcher v6 배포본 해시 일치 확인
  - open_vscode_codex_plan positive smoke 성공
  - outside-root, payloadHash mismatch, missing approval negative smoke 성공

Voice dry-run:
  - wake phrase `오픈클로` stripping 후 intent routing
  - notify, Codex plan, browser read/interact, cancel, critical deny 계약 검증
  - 실제 Kiwi 설치와 마이크/STT/speaker ID는 v7.1로 분리

Kiwi v7.1 prep:
  - Windows uv, Python 3.12, git, FFmpeg, OpenClaw CLI 감지
  - nvidia-smi 감지, PyTorch CUDA 12.8 wheel 설치 후 CUDA available 확인
  - C:\Users\ksg63\projects\kiwi-voice clone + uv venv + requirements 설치 완료
  - Kiwi dashboard http://127.0.0.1:7789 reachable, Playwright snapshot에서 Kiwi Voice 확인
  - Gateway v4와 Kiwi Gateway v3 WebSocket mismatch 때문에 v7.1은 Windows OpenClaw CLI fallback으로 기동
  - repo-local Kiwi Windows readiness probe, config template, transcript dry-run bridge 추가

Kiwi v7.2 final:
  - canonical 기준은 v7.2.15 live dry-run stabilization이다.
  - v7.2.1~v7.2.14는 Web Microphone/STT/wake/command 인식 진단 로그로 아카이브한다.
  - 장기화의 주된 원인은 사용자의 실제 마이크 응답/입력 신호 확인이 늦어진 점과 STT prompt/gate를 과하게 세분화한 점이다.
  - 실제 microphone 전에 Kiwi `OPENCLAW_BIN`은 dry-run shim으로 전환했고 `KIWI_WS_ENABLED=false`를 유지했다.
  - `openclaw agent --message` 형태만 transcript dry-run으로 라우팅했다.
  - dispatcher, OpenClaw real agent, browser, node live execution은 수행하지 않았다.
  - live notify는 `windows_wrapper/notify`, `wouldExecute=false`, `payloadHash` 포함 approval preview로 통과했다.
  - live cancel은 `control/cancel`, `approvalRequest=null`, `wouldExecute=false`로 통과했다.
  - live critical은 `deny/critical`, `approvalRequest=null`, `wouldExecute=false`로 통과했다.
  - Web Microphone 종료 후 Kiwi API는 `state=listening`, `is_processing=false`, `web_audio_clients=0`으로 복귀했다.
  - local Kiwi backup: C:\Users\ksg63\projects\kiwi-voice\backups\openclaw-kiwi-voice-windows\v7.2.15-20260603-143626
  - local Kiwi 실제 내용 변경은 11개 파일 수준이며, `git status`의 대량 modified 표시는 대부분 Windows/WSL line-ending 노이즈로 본다.
  - 상세 진단 로그는 `docs/plans/openclaw_voice_debug_loop_plan.md`의 v7.2 archive를 참조한다.
  - next gate: v7.3 Codex OAuth Voice Planner Contract

Codex OAuth planner v7.3.1:
  - STT transcript dry-run 기본 판단기를 Codex OAuth planner로 전환
  - 기존 keyword-first classifier는 `--legacy-classifier` 진단 옵션으로만 유지
  - `voice_planner.py`가 Codex `exec --sandbox read-only --output-schema`를 호출
  - planner output schema는 OpenAI structured output subset에 맞게 `type`, `required`, `additionalProperties=false` 적용
  - notify, cancel, critical deny, browser_read, browser_interact, prompt injection live planner probe 통과
  - Windows Kiwi dry-run shim 경로도 Codex planner preview만 생성하고 실제 action은 실행하지 않음
  - next gate: v7.4 owner voice + Telegram/manual approval
```

현재 Node는 `system.run`, `system.run.prepare`, `system.which`, `screen.snapshot`, `camera.list`,
`location.get`, `browser.proxy` 같은 command를 선언한다. 개인 PC라 즉시 외부 노출 사고로
보지는 않지만, 이후 voice/agent/browser 자동화와 결합되면 실행 범위가 넓어질 수 있다.

따라서 v4 Browser read와 v5 Browser interact는 Windows dedicated Chrome remote CDP fallback으로 통과 상태로 본다.
v6 VS Code + Codex plan wrapper는 WSL remote read-only route로 통과 상태로 본다.
local managed `openclaw` profile의 wrapper timeout은 별도 추적하되, 기본 Browser lane은 `windows-cdp`를 사용한다.
Kiwi Voice는 Browser/Codex/dispatcher 권한 경계가 확인된 뒤 마지막에 통합한다.

## v0 - 문서 기준선 정리

### 목표

기존 계획 파일을 프로젝트 문서 구조 안으로 옮기고, 실제 구축은 버전 단위로 추적할 수 있게 만든다.

### 작업

- `docs/plans` 디렉터리 생성
- 기존 전체 계획 `.md`, `.html` 이동
- 버전별 진행 계획 추가
- 각 문서의 역할을 `README.md`에 정리

### 산출물

- `docs/plans/README.md`
- `docs/plans/openclaw_kiwi_voice_windows_plan.md`
- `docs/plans/openclaw_kiwi_voice_windows_plan.html`
- `docs/plans/openclaw_kiwi_voice_windows_versioned_plan.md`

### 완료 조건

- 루트에 계획 파일이 흩어져 있지 않다.
- 다음 구축 순서가 v1부터 명확하다.

## v1 - WSL2 Ubuntu + OpenClaw Gateway

### 목표

OpenClaw의 중심 실행 환경을 WSL2 Ubuntu 24.04에 만든다.

### 작업

- Windows에서 Ubuntu 24.04 WSL2 설치
- WSL systemd 활성화
- WSL 안에서 OpenClaw 설치
- Gateway daemon 설치와 상태 확인
- 필요 시 Windows boot wake task 등록

### 검증

```bash
openclaw --version
openclaw doctor
openclaw gateway status
systemctl --user status openclaw-gateway.service --no-pager
```

### 완료 조건

- `openclaw doctor`에서 치명적인 오류가 없다.
- Gateway가 WSL2 user service로 실행된다.
- dashboard 또는 gateway status가 정상 응답한다.

### 보류 항목

- Windows Node pairing
- 브라우저 자동화
- Kiwi Voice 설치

## v2 - Windows Node 최소 연결

### 목표

Windows Node/Tray를 Gateway에 연결하되, 실행 권한은 아직 넓히지 않는다.

### 작업

- Windows Node/Tray 설치
- Gateway URL을 `ws://127.0.0.1:18789`로 설정
  - `localhost`가 IPv6 `::1`로 해석되어 timeout이 나면 `127.0.0.1`을 우선 사용한다.
- Node Mode와 Start with Windows 설정
- WSL에서 pending device를 확인한다.
- setup code 경로로 이미 paired/connected가 되면 pending approve를 추가로 실행하지 않는다.
- `allowCommands` 또는 Node declared commands는 기록만 하고, 위험 command는 v3 전까지 호출하지 않는다.

### v2에서 실제 테스트할 명령

```text
device.info
device.status
system.notify
screen.snapshot
```

`system.run`, raw shell, dispatcher deploy, browser click/type, camera/location/screen.record는 v2에서
테스트하지 않는다. `system.run`은 v3에서 dispatcher-only 정책까지 준비한 뒤에만 다룬다.

### 검증

```bash
openclaw nodes status
openclaw nodes pending --json
openclaw nodes list --connected --json
openclaw nodes describe --node <WINDOWS_NODE_ID> --json
openclaw nodes invoke --node <WINDOWS_NODE_ID> --command device.status --params '{}'
openclaw nodes invoke --node <WINDOWS_NODE_ID> --command system.notify --params '{"title":"OpenClaw","body":"Node OK"}'
openclaw nodes invoke --node <WINDOWS_NODE_ID> --command screen.snapshot --params '{"screenIndex":0,"format":"png"}'
```

### 완료 조건

- Node가 paired/connected 상태로 유지된다.
- Windows 알림, 상태 조회, 단일 화면 snapshot이 성공한다.
- pending request가 비어 있다.
- `system.run`, `screen.record`, camera, location 계열은 호출하지 않았다.
- Node가 privacy-heavy command를 선언하면 v3에서 정책 잠금 전까지 차단 대상으로 기록한다.

## v3 - Central Dispatcher-Only `system.run` + Exec Approvals

### 목표

Windows에서 실행 가능한 명령을 단일 central dispatcher wrapper로 제한하고, Gateway와 Node 양쪽 approval 경계를 확인한다.

### 작업

- repo 내부 `scripts/win/Invoke-OpenClawAction.ps1` 초안 작성
- 배포 시에만 `C:\OpenClawActions\Invoke-OpenClawAction.ps1`로 복사
- dispatcher action enum을 `notify`, `open_url_readonly`, `open_vscode_codex_plan`, `open_app_allowlisted`, `run_task_recipe`로 제한
- Windows Node `exec-policy.json`을 기본 deny로 설정
- Gateway `exec-approvals.json` 또는 `tools.exec.*` 정책을 보수적으로 설정
- `system.run`을 `allowCommands`에 추가

### 다음 batch - Dispatcher Deploy + Windows Node 정책 잠금

v2가 완료된 현재 시점의 다음 작업이다. 이 batch는 Windows 실제 배포 경로와 Windows Node local
exec policy를 건드리므로, Browser/Kiwi보다 먼저 수행한다.

#### 사전 조건

```text
- repo: main...origin/main clean
- Gateway: reachable
- Gateway approvals: security=allowlist, ask=always, askFallback=deny, autoAllowSkills=off
- Windows Node: Known >= 1, Paired >= 1, Connected >= 1
- safe smoke: device.status, system.notify, screen.snapshot 성공
- scripts/win/Invoke-OpenClawAction.ps1 PowerShell parser OK
```

#### 작업 순서

```text
1. Windows Node local exec-policy 기존 파일을 timestamp backup으로 저장한다.
2. C:\OpenClawActions 디렉터리를 만든다.
3. repo의 scripts/win/Invoke-OpenClawAction.ps1만 C:\OpenClawActions로 복사한다.
4. %LOCALAPPDATA%\OpenClawTray\exec-policy.json에 dispatcher-only 정책을 적용한다.
5. Gateway approvals가 풀리지 않았는지 다시 확인한다.
6. dispatcher notify action만 approved smoke test로 실행한다.
7. payloadHash mismatch request가 거부되는지 확인한다.
8. raw powershell/cmd/python -c/node -e가 차단되는지 확인한다.
```

#### 이번 batch에서 금지

```text
- open_vscode_codex_plan 실기 실행
- run_task_recipe 실기 실행
- Browser click/type
- camera/location/screen.record
- Kiwi Voice 연결
- Gateway approvals 완화
- Node policy를 default allow로 변경
```

#### 롤백

```text
1. Windows Node Mode를 끈다.
2. %LOCALAPPDATA%\OpenClawTray\exec-policy.json을 default deny 또는 백업 파일로 되돌린다.
3. 필요하면 C:\OpenClawActions\Invoke-OpenClawAction.ps1을 삭제한다.
4. Gateway approvals가 allowlist/ask always/deny/autoAllowSkills=off인지 재확인한다.
```

### 권장 wrapper

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OpenClawActions\Invoke-OpenClawAction.ps1 -RequestJsonBase64 <request>
```

### 권장 정책

```text
default: deny
allowlist: central dispatcher command only
ask: always
askFallback: deny
autoAllowSkills: false
strictInlineEval: true
```

### 검증

```bash
openclaw approvals get --gateway
openclaw approvals get --node <WINDOWS_NODE_ID>
openclaw nodes invoke --node <WINDOWS_NODE_ID> --command system.execApprovals.get
```

테스트는 두 갈래로 한다.

- 허용 dispatcher action: 승인 후 실행되어야 한다.
- 임의 명령: 차단되어야 한다.

### 완료 조건

- 임의 `powershell`, `cmd`, `python -c`, `node -e` 실행이 자동 통과하지 않는다.
- dispatcher 명령도 사용자 승인 없이는 실행되지 않는다.
- 실패 시 전체 차단 정책으로 즉시 롤백할 수 있다.

## v4 - 브라우저 `browse-read`

### 목표

격리된 `openclaw` 브라우저 프로필에서 페이지 열기, 읽기, snapshot, screenshot만 먼저 안정화한다.

### 작업

- OpenClaw Browser 활성화
- 기본 프로필을 `openclaw`로 설정
- Windows Node browser proxy 또는 remote CDP fallback 중 하나 선택
- 개인 `user` 프로필은 사용하지 않는다.
- raw CDP probe로 OpenClaw wrapper 문제와 Chromium/CDP 문제를 분리한다.

### 허용 범위

```text
open URL
focus tab
read page
snapshot
screenshot
scroll
```

### 검증

```bash
node scripts/wsl/cdp_probe.mjs --cdp-url http://127.0.0.1:18800
node scripts/wsl/cdp_probe.mjs --cdp-url http://127.0.0.1:9222 --out .debugloop/artifacts/browser/windows-cdp-probe.json
python3 scripts/wsl/browser_probe.py --profile openclaw --url https://example.com --snapshot-limit 200
python3 scripts/wsl/browser_probe.py --profile windows-cdp --url https://example.com --snapshot-limit 200
openclaw browser --browser-profile openclaw doctor --deep
openclaw browser --browser-profile openclaw start
openclaw browser --browser-profile openclaw open "https://example.com"
openclaw browser --browser-profile openclaw snapshot
openclaw browser --browser-profile openclaw screenshot --full-page --out /tmp/browser-read-ok.png
```

### 현재 진단 결과

```text
local openclaw before reset:
  raw CDP /json/version, /json/list, Browser.getVersion: OK
  raw CDP Runtime.evaluate/Page.captureScreenshot/Accessibility.getFullAXTree: timeout
  rendererCount: 0

local openclaw after reset:
  raw CDP Runtime.evaluate/Page.captureScreenshot/Accessibility.getFullAXTree: temporarily OK
  rendererCount: 2+ immediately after reset, later regressed to 0
  OpenClaw doctor live-snapshot/snapshot/screenshot: timeout

linux headless/noSandbox retry:
  raw CDP: OK
  OpenClaw wrapper: timeout
  config restored from backup

windows-cdp fallback:
  raw CDP Runtime.evaluate/Page.captureScreenshot/Accessibility.getFullAXTree: OK
  OpenClaw browser_probe status/tabs/open/doctor/snapshot/screenshot/console/errors: OK
```

판정:

```text
초기 문제는 local managed openclaw profile의 renderer/CDP 상태 손상으로 시작했다.
profile reset으로 raw CDP가 일시 복구됐지만 local managed profile은 재시작 후 rendererCount 0 상태가 재발했다.
OpenClaw wrapper connectOverCDP timeout도 local managed profile에서 계속 남았다.
운영 기본값은 Windows dedicated Chrome remote CDP profile windows-cdp로 전환한다.
```

### 완료 조건

- 격리 프로필에서만 브라우저가 열린다.
- page snapshot과 screenshot이 정상 생성된다.
- 로그인된 개인 브라우저 프로필을 사용하지 않는다.

## v5 - 브라우저 `browse-interact`

### 목표

클릭, 입력, 선택 같은 상호작용을 허용하되, side-effect 동작은 별도 승인으로 남긴다.

### 작업

- `browse-interact` 승인 문구 정의
- repo-local 테스트 페이지에서 click/type/fill/select 검증
- 클릭/입력 후 fresh snapshot을 의무화
- localhost fixture용 `browser.ssrfPolicy.allowedHostnames`를 `127.0.0.1`, `localhost`로 제한
- 다운로드, 업로드, 제출, 전송, 결제는 별도 high-impact 승인으로 분리

### 허용 범위

```text
click
type/fill
press
select
hover
scroll
```

### 항상 별도 승인할 동작

```text
로그인된 user profile 사용
폼 제출
메일/메시지/게시물 전송
구매/결제/예약/취소
파일 다운로드
파일 업로드
권한 요청 허용
위치/카메라/마이크 접근
```

### 완료 조건

- `windows-cdp` profile에서 click/type/fill/select가 성공한다.
- 각 interact 후 fresh snapshot에서 상태 변화가 확인된다.
- interact screenshot artifact가 생성된다.
- side-effect 버튼 앞에서 멈추고 사용자에게 다시 확인한다.
- 웹 페이지 내용이 agent 지침을 덮어쓰지 못하도록 instruction에 명시한다.

현재 검증:

```bash
python3 scripts/wsl/browser_interact_probe.py --profile windows-cdp
python3 scripts/wsl/browser_interact_probe.py --profile user --no-write-log
```

```text
status/open/initial_snapshot: OK
click/type/fill/select: OK
fresh snapshot after each interact: OK
screenshot artifact: .debugloop/artifacts/browser/browser-interact-ok.png
user profile negative test: blocked
```

주의:

```text
같은 windows-cdp profile에서 read probe와 interact probe를 병렬 실행하지 않는다.
OpenClaw browser refs는 최신 snapshot에 묶이므로 같은 profile의 tab/snapshot 작업은 순차 실행한다.
```

## v6 - VS Code + Codex Read-Only Plan

### 목표

음성 명령으로 개발 프로젝트를 열더라도 Codex는 먼저 read-only plan만 작성하게 한다.

### 작업

- `Invoke-OpenClawAction.ps1`의 `open_vscode_codex_plan` action 구현
- 허용 프로젝트 루트에 현재 WSL repo UNC 경로 추가
- WSL UNC project는 `code --remote wsl+Ubuntu-22.04 -r /home/user/projects/openclaw-kiwi-voice-windows`로 열기
- Codex CLI는 `wt.exe`에서 `wsl.exe -d Ubuntu-22.04 --cd <project> -- /home/user/.npm-global/bin/codex`로 실행
- Codex CLI를 read-only sandbox와 approval on-request로 고정
- Windows Codex를 UNC repo에 직접 붙이는 fallback은 사용하지 않기

### 검증

```bash
python3 scripts/wsl/codex_plan_probe.py --case positive --dispatcher deployed --execute
python3 scripts/wsl/codex_plan_probe.py --case outside-root --dispatcher deployed --execute
python3 scripts/wsl/codex_plan_probe.py --case hash-mismatch --dispatcher deployed --execute
python3 scripts/wsl/codex_plan_probe.py --case missing-approval --dispatcher deployed --execute
```

### 완료 조건

- 허용 루트 밖의 프로젝트 경로는 거부된다.
- VS Code가 WSL remote로 현재 repo를 연다.
- Codex가 WSL repo에서 파일을 수정하지 않고 read-only 계획만 작성한다.
- workspace-write 전환은 별도 승인 절차로만 가능하다.

### 결과 - 2026-06-02

```text
positive: deployed dispatcher → VS Code WSL remote + WSL Codex read-only plan 시작 성공
negative: C:\Windows outside-root 거부 성공
negative: payloadHash mismatch 거부 성공
negative: approvedByUser=false 거부 성공
approvals: allowlist + ask=always + askFallback=deny + autoAllowSkills=off 유지
```

## v7 - Kiwi Voice 통합

### 목표

Windows native Kiwi Voice를 붙여 “호출어 감지 → 계획 요약 → 승인 요청”까지 연결한다.

### v7.0 - Voice dry-run contract

실제 Kiwi 설치 전에 repo-local dry-run으로 음성 utterance routing 계약을 고정한다.

작업:

- wake phrase `오픈클로`와 쉼표/공백 제거 후 intent 분류
- “테스트 알림 보내줘”, “응답 테스트”는 dispatcher `notify` approval request로 라우팅
- Codex/계획/플랜 요청은 현재 WSL repo 대상 `open_vscode_codex_plan` approval request로 라우팅
- browser read와 interact를 `windows-cdp` profile 기준 dry-run lane으로 분리
- “취소”는 control cancel로 처리하고 approval request를 만들지 않음
- 결제, 삭제, Gmail/메일/메시지 발송, 비밀번호/OTP, raw shell은 critical deny

검증:

```bash
python3 scripts/wsl/voice_e2e_probe.py
python3 scripts/wsl/voice_dry_run.py --utterance "오픈클로, 테스트 알림 보내줘"
python3 scripts/wsl/voice_dry_run.py --utterance "오픈클로, Codex로 현재 프로젝트 다음 계획 세워줘"
python3 scripts/wsl/voice_dry_run.py --utterance "취소"
python3 scripts/wsl/voice_dry_run.py --utterance "오픈클로, 결제하고 Gmail로 비밀번호 보내"
```

완료 조건:

- notify/Codex plan은 approval request만 만들고 실행하지 않는다.
- critical intent는 approval request도 만들지 않는다.
- browser intent는 dispatcher request가 아니라 browser lane dry-run으로만 남는다.

### v7.1 - Windows Kiwi 설치와 dry-run 연결

상태:

```text
Windows tools: uv, python 3.12, git, FFmpeg, OpenClaw CLI available
Kiwi repo: C:\Users\ksg63\projects\kiwi-voice cloned
Kiwi venv: created with uv
Dashboard: reachable at http://127.0.0.1:7789
Repo prep: kiwi_windows_probe.py, kiwi_transcript_dry_run.py, config.yaml.template 추가
```

### 작업

- Windows Python 환경에서 `uv venv`로 가상환경 생성
- `where.exe python`으로 interpreter 위치 확인
- `uv pip install -r requirements.txt`로 의존성 설치
- PyTorch가 필요하면 cu128 index URL을 명시
- `config.yaml`에 한국어, faster-whisper, text wake word 설정
- `OPENCLAW_BIN`은 v7.2 전까지 실제 OpenClaw CLI로 두지 않는다.
- speaker ID owner 등록, Telegram approval, Gateway v4 WebSocket 호환은 v7.2 이후 별도 batch로 둔다.

진행 gate:

```bash
python3 scripts/wsl/kiwi_windows_probe.py
```

`kiwi_windows_probe.py`가 ready가 아니면 v7.2 마이크/STT/speaker owner 등록으로 넘어가지 않는다.

### v7.2 - Live transcript dry-run adapter

상태:

```text
목표: Kiwi live STT transcript가 실제 실행 없이 repo dry-run 계약으로만 흐르게 만든다.
Windows shim: C:\Users\ksg63\projects\kiwi-voice\dry-run-openclaw.cmd
Repo helper: scripts/win/Invoke-KiwiDryRunOpenClaw.ps1
Runtime log: .debugloop/runs/kiwi-live-dry-run.jsonl
```

작업:

- `scripts/win/kiwi-dry-run-openclaw.cmd`와 `Invoke-KiwiDryRunOpenClaw.ps1`을 Kiwi repo에 복사한다.
- Kiwi `.env`를 timestamp backup 후 `OPENCLAW_BIN=C:\Users\ksg63\projects\kiwi-voice\dry-run-openclaw.cmd`로 바꾼다.
- `KIWI_WS_ENABLED=false`를 유지한다.
- shim은 `--version`과 `agent --session-id ... --message ... --timeout ...`만 허용한다.
- transcript는 WSL repo의 `kiwi_transcript_dry_run.py`로 전달하고 `wouldExecute=false`를 검증한다.
- unsupported command는 deny로 기록하고 nonzero로 종료한다.

검증:

```bash
python3 scripts/wsl/kiwi_live_dry_run_probe.py
python3 scripts/wsl/kiwi_windows_probe.py
```

완료 조건:

- notify/Codex transcript는 approval preview만 만들고 실행하지 않는다.
- cancel/critical transcript는 approval request를 만들지 않는다.
- `.env`의 `OPENCLAW_BIN`이 dry-run shim을 가리킨다.
- 실제 OpenClaw agent, dispatcher, browser, node command는 호출하지 않는다.

### v7.2.1 - Web Microphone dry-run smoke

상태:

```text
dashboard: http://127.0.0.1:7789 reachable
browser profile: windows-cdp dedicated CDP
Kiwi API before smoke: state=idle, web_audio_clients=0
Web Microphone UI: Connected - listening
Kiwi API during smoke: web_audio_clients=1
Event log: WEB_CLIENT_CONNECTED
Dry-run JSONL: unchanged, no live transcript event detected
Cleanup: Web Microphone disconnected, web_audio_clients=0
```

판정:

- Web Microphone transport는 성공했다.
- 실제 OpenClaw agent, dispatcher, browser, node command는 호출되지 않았다.
- STT/wake가 transcript를 만들지 못해 notify/cancel/critical live dry-run route 검증은 미완료다.

다음 작업:

- v7.2.2에서 browser microphone permission, Windows input device, dashboard audio level, STT/VAD/wake threshold를 분리 진단한다.
- dry-run shim은 유지하고, transcript가 생긴 뒤에만 notify/cancel/critical live smoke를 반복한다.

### v7.2.2 - Audio level / STT / wake calibration

상태:

```text
scope: diagnostics only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
runtime artifacts: .debugloop/artifacts/kiwi, .debugloop/runs
local Kiwi backup: C:\Users\ksg63\projects\kiwi-voice\.codex-backups\v7.2.2-20260602-152419
```

작업:

- `kiwi_runtime_capture.py`로 Windows Kiwi를 로그 캡처 모드로 재시작한다.
- `kiwi_audio_ws_probe.mjs`로 `/api/audio`에 silence/tone PCM을 넣어 WebAudioBridge speech gate를 확인한다.
- `kiwi_browser_mic_level_probe.mjs`로 dashboard tab의 실제 microphone RMS/peak를 측정한다.
- 로그에서 `WEB_AUDIO`, `Speech segment`, `External audio submitted`, `PROCESS`, `WHISPER`, `WAKE`, `OPENCLAW`만 추적한다.
- 실제 OpenClaw agent, dispatcher, browser, node action 흔적이 있으면 즉시 중단한다.
- local Kiwi source의 Silero `torch.hub.load` prompt는 `trust_repo=True`로 제거한다.
- Whisper `language="ru"` hardcode는 config language 기반으로 보정한다.

판정:

- synthetic tone이 segment를 만들고 실제 mic가 못 만들면 Windows/browser input gain 문제로 둔다.
- segment는 생기지만 STT가 실패하면 local Kiwi checkout을 backup한 뒤 config language 기반 STT 보정만 검토한다.
- transcript가 dry-run shim에 도달한 뒤에만 notify/cancel/critical live smoke를 재시도한다.

결과:

- silence probe는 speech segment 없이 통과했다.
- tone probe는 `WEB_AUDIO Speech segment`, `External audio submitted`, `PROCESS`, `WHISPER`까지 도달했다.
- Whisper detected language는 `ko`, probability `1.00`이었다.
- browser mic probe는 permission `granted`였지만 maxRms `0.000129`로 gate `0.015` 미만이었다.
- 현 blocker는 Kiwi code path가 아니라 Windows/browser microphone input level 또는 speaking-window 재측정이다.

### v7.2.3 - Microphone input gain smoke

상태:

```text
scope: microphone input calibration only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
live dispatcher/browser/node execution: not attempted
```

결과:

- Kiwi readiness probe와 live dry-run shim probe는 통과했다.
- Gateway approvals는 `allowlist + ask=always + askFallback=deny + autoAllowSkills=off`를 유지했다.
- Windows Node는 `Known/Paired/Connected = 1/1/1`을 유지했다.
- browser mic probe는 `마이크(USB Audio Device) (0c76:160a)`를 선택된 audio track으로 기록했다.
- `maxRms=0.00015`, `aboveThresholdCount=0`으로 speech gate `0.015`에 도달하지 못했다.
- live Web Microphone dry-run smoke는 중단했고, transcript shim log 검증은 다음 반복으로 유지한다.

다음:

- Windows Sound input에서 같은 USB microphone의 input meter가 말할 때 움직이는지 확인한다.
- input volume을 80-100으로 설정하고 mute/driver/device routing을 확인한다.
- `kiwi_browser_mic_level_probe.mjs`가 `maxRms >= 0.015`를 기록한 뒤 live notify/cancel/critical smoke를 재시도한다.

### v7.2.4 - Windows mic native signal verification

상태:

```text
scope: Windows microphone signal diagnosis only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
live dispatcher/browser/node execution: not attempted
```

결과:

- Windows Sound settings를 열어 input device/volume 확인을 유도했다.
- `windows-cdp` attachOnly profile이 중지되어 dedicated Chrome CDP를 다시 띄웠다.
- browser standard probe는 `maxRms=0.000141`, raw-audio probe는 `maxRms=0.000135`였다.
- Windows native `sounddevice` capture helper를 추가했고 USB Audio Device native RMS는 `0.000015`였다.
- input device indexes `1, 8, 18, 23, 0, 7`을 스캔했지만 유효한 speech-level signal은 없었다.
- device index `23`은 `PaErrorCode -9996`으로 열리지 않았다.
- live Web Microphone dry-run smoke는 중단했고, 다음 반복 전 Windows mic hardware/driver/mute/gain/routing 수동 보정이 필요하다.

다음:

- Windows input meter가 발화에 반응하는지 먼저 확인한다.
- USB mic cable/adapter, mute switch, Windows input volume, driver, privacy permission, default communication device를 점검한다.
- `kiwi_windows_audio_probe.py`와 `kiwi_browser_mic_level_probe.mjs`가 speech gate를 통과한 뒤 live smoke를 재시도한다.

### v7.2.5 - Windows mic signal recovery gate

상태:

```text
scope: Windows microphone signal retry only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
live dispatcher/browser/node execution: not attempted
```

결과:

- Kiwi readiness, live dry-run shim probe, Gateway approvals, Windows Node 연결은 모두 정상이다.
- Windows Sound settings를 다시 열어 input device/volume 확인을 유도했다.
- Windows native USB Audio Device retry는 `rms=0.000015`, `peak=0.000031`로 여전히 near-silence였다.
- browser standard probe는 `maxRms=0.000086`, raw-audio probe는 `maxRms=0.000136`이었다.
- `aboveThresholdCount=0`으로 Kiwi speech gate `0.015`를 통과하지 못했다.
- live Web Microphone dry-run smoke는 중단했다.

다음:

- Windows input meter가 실제 발화에 강하게 반응하도록 device/gain/mute/driver/routing을 먼저 고친다.
- browser probe에서 `maxRms >= 0.015` 또는 `aboveThresholdCount > 0`가 나온 뒤 notify/cancel/critical live dry-run smoke를 재시도한다.
- 그 전에는 owner voice, Telegram approval, Gateway v4 WebSocket compatibility, dispatcher live execution으로 넘어가지 않는다.

### v7.2.6 - Windows audio device audit

상태:

```text
scope: Windows/browser input device ranking only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
live dispatcher/browser/node execution: not attempted
```

구현:

- `kiwi_windows_audio_probe.py`에 `--scan-all`, `--per-device-ms`, `--min-rms`를 추가했다.
- `kiwi_browser_mic_level_probe.mjs`에 `--scan-inputs`, `--per-device-ms`, `--min-rms`를 추가했다.
- `Taskfile.yml`에 `kiwi:windows-audio-scan`, `kiwi:browser-mic-scan`을 추가했다.

결과:

- native scan은 Windows input devices 전체를 ranking했지만 best RMS가 `0.000015`였다.
- browser scan은 `default - 마이크(USB Audio Device) (0c76:160a)`가 best였고 `maxRms=0.000161`, `maxPeak=0.000636`이었다.
- 모든 candidate의 `aboveThresholdCount=0`이며 minRms `0.015`를 통과하지 못했다.
- live Web Microphone dry-run smoke는 중단했다.

다음:

- Windows input meter가 실제 발화에 충분히 반응할 때까지 physical mic, cable/adapter, mute switch, gain, privacy permission, driver, routing을 먼저 고친다.
- scan에서 gate를 통과한 device index/deviceId가 생긴 뒤 v7.2.7 live dry-run smoke를 진행한다.

### v7.2.7 - Known-good mic recovery and live dry-run smoke gate

상태:

```text
scope: microphone signal gate + live dry-run smoke only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
live dispatcher/browser/node execution: not attempted
```

구현:

- `kiwi_windows_audio_probe.py`의 scan host timeout과 Windows output path 처리를 보정했다.

결과:

- native scan은 끝까지 완료되었고 best device는 USB Audio Device index `1`이었다.
- native best는 `rms=0.006402`, `peak=0.654480`으로 개선되었지만 minRms `0.015`에는 못 미쳤다.
- browser scan은 `communications - 마이크(USB Audio Device) (0c76:160a)`가 best였고 `maxRms=0.047101`, `aboveThresholdCount=2`로 gate를 통과했다.
- dashboard Web Microphone을 연결해 live dry-run smoke를 시도했지만 `.debugloop/runs/kiwi-live-dry-run.jsonl`은 증가하지 않았다.
- Kiwi log는 WebAudio speech segment를 만들었지만 대부분 `0.2s-0.3s`로 잘려 Whisper가 `Audio too short`로 건너뛰었다.
- 하나의 `1.4s` segment는 Whisper까지 갔지만 hallucination으로 skip되어 transcript가 dry-run shim에 도달하지 않았다.
- Web Microphone은 종료 후 `web_audio_clients=0`으로 복귀했다.

다음:

- v7.2.8에서 WebAudioBridge의 segment buffering/minimum duration을 보정한다.
- live notify/cancel/critical smoke는 transcript가 dry-run shim에 도달한 뒤 재시도한다.

### v7.2.8 - WebAudioBridge segment buffering fix

상태:

```text
scope: local Windows Kiwi WebAudioBridge buffering only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
OpenClaw approvals / dispatcher / Node policy: unchanged
```

구현:

- Windows Kiwi checkout을 timestamp backup 후 수정했다.
- `audio_bridge.py`는 speech end를 chunk count가 아니라 duration으로 판단한다.
- `config_loader.py`는 `web_audio` tuning 값을 읽는다.
- local `config.yaml`에는 `min_submit_seconds: 1.0`, `end_silence_seconds: 0.8`만 추가했다.
- `tests/test_audio_bridge.py`에 최소 duration 전 short segment buffering 검증을 추가했다.

검증:

- `py_compile` 통과: `kiwi\api\audio_bridge.py`, `kiwi\config_loader.py`
- `tests\test_audio_bridge.py`: 10 passed
- browser mic scan: default USB mic `maxRms=0.037766`, `aboveThresholdCount=7`
- synthetic WebAudio: `Speech segment: 2.0s`, `External audio submitted: 2.0s`
- dashboard Web Microphone: `Speech segment: 1.7s`, `External audio submitted: 1.7s`
- `.debugloop/runs/kiwi-live-dry-run.jsonl`은 130줄 그대로 유지되어 실제 실행 요청은 없었다.
- 종료 후 `web_audio_clients=0`으로 복귀했다.

다음:

- v7.2.9에서 Whisper hallucination filter, timestamp hallucination, Korean prompt/threshold를 조정한다.
- notify/cancel/critical live smoke는 transcript가 dry-run shim에 도달한 뒤 재시도한다.

### v7.2.9 - Whisper/STT filter tuning

상태:

```text
scope: local Windows Kiwi Whisper/STT filtering only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
OpenClaw approvals / dispatcher / Node policy: unchanged
```

구현:

- Windows Kiwi checkout을 timestamp backup 후 수정했다.
- `config_loader.py`는 `stt.whisper_*` tuning 값을 읽는다.
- `service.py`는 config language와 Whisper filter tuning 값을 `ListenerConfig`로 전달한다.
- `listener.py`는 segment reject helper로 no_speech, avg_logprob, timestamp overrun을 판정한다.
- local `config.yaml`에는 Korean prompt/threshold 값을 추가했다.
- `tests/test_listener_whisper_filter.py`에 segment filter unit test를 추가했다.

검증:

- `py_compile` 통과: `kiwi\listener.py`, `kiwi\config_loader.py`, `kiwi\service.py`
- `tests\test_config.py`, `tests\test_smoke.py`, `tests\test_listener_whisper_filter.py`: 23 passed
- browser mic scan: USB mic `maxRms=0.018282`, `aboveThresholdCount=1`
- dashboard Web Microphone: `Speech segment` 1.2s-3.1s, detected language `ko`
- live STT: repeated `"응답 테스트."`, one segment `"테스트 알림 보내줘."`
- live command did not reach dry-run shim because the wake phrase `"오픈클로"` was not preserved.
- 종료 후 `web_audio_clients=0`으로 복귀했다.

다음:

- v7.2.10에서 prompt hallucination을 줄이고 wake phrase 보존을 우선 조정한다.
- 후보: initial prompt를 wake-only로 축소, wake phrase typo/autocorrect 강화, two-step wake-only then command smoke, 필요 시 `medium` model/backend 비교.
- notify/cancel/critical live smoke는 wake phrase 또는 dialog-mode command가 dry-run shim에 도달한 뒤 재시도한다.

### v7.2.10 - Korean wake detector fix

상태:

```text
scope: local Windows Kiwi Korean wake detector compatibility only
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
OpenClaw approvals / dispatcher / Node policy: unchanged
```

구현:

- Windows Kiwi checkout을 timestamp backup 후 수정했다.
- `WakeWordDetector`는 configured Unicode wake word를 그대로 보존한다.
- Korean wake aliases `오픈클로`, `오픈 클로`, `오픈클로우`, `오픈 클로우`를 지원한다.
- command extraction은 Unicode `\w`를 사용해 한글 명령을 유효 command로 인정한다.
- local `config.yaml`의 `whisper_initial_prompt`를 `"오픈클로"`로 축소했다.
- `tests/test_wake_word_detector.py`에 Korean wake unit test를 추가했다.

검증:

- `py_compile` 통과: `kiwi\listener.py`, `kiwi\config_loader.py`, `kiwi\service.py`
- `tests\test_config.py`, `tests\test_smoke.py`, `tests\test_listener_whisper_filter.py`, `tests\test_wake_word_detector.py`: 28 passed
- browser mic scan: USB mic `maxRms=0.018143`, `aboveThresholdCount=2`
- dashboard Web Microphone: `Speech segment` 1.7s-2.8s, detected language `ko`
- live STT: unrelated/hallucinated Korean text, no wake phrase
- live command did not reach dry-run shim; JSONL increases came from background `debug_forever` dry-run probes.
- 종료 후 `web_audio_clients=0`으로 복귀했다.

Archive note:

- v7.2.11에서 STT model/backend를 비교한다.
- v7.2.11 결과, `small + "오픈클로"` prompt가 wake phrase는 3/3 인식했지만 command body는 0/3이었다.
- v7.2.12 결과, wake-only two-step flow도 command-only STT가 `테스트 알림 보내줘`를 0/3으로 놓쳐 blocked다.
- v7.2.13~v7.2.14의 STT/command gate 비교는 진단 기록으로 보관한다.
- v7.2.15가 이 구간을 supersede하며, 세션 내 로컬 Kiwi 보정값을 고정하고 notify/cancel/critical live dry-run smoke를 통과했다.

### v7.2.15 - Live dry-run stabilization

```text
상태: 통과
범위: dry-run only, no dispatcher/OpenClaw agent/browser/node live execution
local backup: C:\Users\ksg63\projects\kiwi-voice\backups\openclaw-kiwi-voice-windows\v7.2.15-20260603-143626
OPENCLAW_BIN: dry-run-openclaw.cmd
KIWI_WS_ENABLED: false
Gateway approvals: allowlist + ask=always + askFallback=deny + autoAllowSkills=off
```

로컬 Kiwi 보정:

- `config.yaml`: `stt.whisper_dialog_prompt: "테스트 알림 보내줘"` 유지
- `listener.py`: `ListenerConfig.wake_word`가 detector에 우선 적용
- `text_processing.py`: 한국어 짧은 명령/critical marker `보내`, `삭제`, `결제`를 complete phrase로 처리
- `openclaw_cli.py`: dry-run shim 사용 시 첫 system prompt를 제거하고 실제 transcript command만 전달

검증:

- notify live dry-run: `windows_wrapper/notify`, `wouldExecute=false`, `payloadHash` 포함 approval preview
- cancel live dry-run: `control/cancel`, `approvalRequest=null`, `wouldExecute=false`
- critical live dry-run: `deny/critical`, `approvalRequest=null`, `wouldExecute=false`
- Web Microphone 종료 후 `state=listening`, `is_processing=false`, `web_audio_clients=0`

다음:

- v7.3에서는 owner voice/Telegram보다 먼저 Codex OAuth planner contract를 고정한다.
- dispatcher live execution은 approval-layer gate 이후로 유지한다.

### v7.3 - Codex OAuth Voice Planner Contract

```text
상태: v7.3.1 통과
범위: repo-local dry-run planner bridge
planner: /home/user/.npm-global/bin/codex exec
execution: 없음
```

새 기본 흐름:

```text
Kiwi wake/STT/speaker
→ STT transcript
→ Codex OAuth voice planner
→ planner output schema 검증
→ post-planner policy / approval / dispatcher validation
→ 승인된 작업만 browser lane 또는 Windows dispatcher lane으로 실행
```

v7.3부터는 `취소`, 결제, Gmail, 비밀번호, 삭제, raw shell 같은 문장도
Codex planner로 먼저 전달한다. 기존 repo dry-run classifier의 keyword-first
cancel/critical deny 처리는 v7.2 archive 또는 legacy fallback으로만 둔다.

Planner 출력 계약:

```text
decision: cancel | deny | clarify | request_approval
lane: none | windows_wrapper | browser_read | browser_interact | codex_plan
riskTier: low | medium | high | critical
action: dispatcher/browser/Codex action 또는 null
userFacingPlan: 승인 전에 사용자에게 보여줄 짧은 계획
approvalRequired: 실행 가능 작업은 true
wouldExecute: dry-run에서는 항상 false
```

Codex OAuth planner 기본 호출:

```bash
/home/user/.npm-global/bin/codex exec \
  -C /home/user/projects/openclaw-kiwi-voice-windows \
  --sandbox read-only \
  --output-schema schemas/voice-planner-output.schema.json \
  "<voice planner prompt>"
```

Post-planner enforcement:

- schema 불일치, 허용 action 밖 출력, approval 없는 실행은 거부한다.
- Browser lane은 `windows-cdp` 프로필과 기존 browser permission 정책을 유지한다.
- Windows action은 중앙 dispatcher와 payloadHash 검증을 통과해야 한다.
- high/critical risk는 planner가 허용하더라도 fresh external approval 없이는 실행하지 않는다.

v7.3.1 검증 결과:

- `python3 scripts/wsl/voice_planner_probe.py` 통과
- `python3 scripts/wsl/kiwi_live_dry_run_probe.py --skip-env-check` 통과
- notify는 approval preview만 생성하고 `wouldExecute=false` 유지
- cancel은 `decision=cancel`, `lane=none`, approval 없음
- critical/prompt injection은 `decision=deny`, `riskTier=critical`, approval 없음
- Browser read/interact는 각각 `browser_read`, `browser_interact` approval preview 생성

v7.4 이후:

- owner voice 등록
- Telegram/manual approval 연결
- Gateway v4 WebSocket compatibility 또는 CLI fallback 최종 결정

### Windows 설치 원칙

```powershell
uv venv venv
.\venv\Scripts\activate
where.exe python
uv pip install -r requirements.txt
```

PyTorch를 직접 설치해야 하는 경우:

```powershell
uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128
```

### 검증

```powershell
python -m kiwi
```

v7.2 이후 선택적 음성 테스트:

```text
오픈클로, 응답 테스트        -> dry-run JSONL only
취소                         -> no approval request
오픈클로, 결제하고 Gmail로 비밀번호 보내 -> critical deny
```

### 완료 조건

- 호출어와 STT가 dry-run adapter로만 연결된다.
- owner 음성 등록과 Telegram approval은 아직 완료 조건이 아니다.
- 위험 명령은 approval request 없이 deny된다.
- Kiwi는 executor가 아니라 planner 승인 흐름의 입구로 동작한다.

## v8 - End-to-End 운영 안정화

### 목표

전체 흐름을 실사용 가능한 수준으로 굳히고, 문제 발생 시 빠르게 되돌릴 수 있게 한다.

### 작업

- Gateway, Windows Node, Browser, Codex, Kiwi smoke test를 하나의 체크리스트로 묶기
- 설정 파일 백업 절차 작성
- 로그 확인 위치 정리
- 브라우저 자동화 끄기, `system.run` 전체 차단, Kiwi 중지 롤백 절차 검증
- 위험도별 승인 정책을 실제 문구로 확정

### End-to-End 검증 시나리오

```text
1. "오픈클로" 호출
2. 브라우저에서 문서 검색 요청
3. planner가 browse-interact 작업 요약
4. 사용자가 승인
5. 격리 openclaw 프로필에서 검색, 클릭, 페이지 읽기, 스크린샷
6. 결과 요약
7. side-effect 동작 없이 종료
```

```text
1. "오픈클로" 호출
2. VS Code에서 프로젝트 열고 Codex 계획 요청
3. planner가 read-only plan 작업 요약
4. 사용자가 승인
5. dispatcher-only system.run 승인
6. VS Code와 Codex plan 실행
7. 파일 수정 없이 종료
```

### 완료 조건

- 브라우저와 Codex 시나리오가 각각 끝까지 성공한다.
- 금지 동작이 자동 실행되지 않는다.
- 각 컴포넌트별 롤백 절차가 실제로 동작한다.

## 우선순위 있는 다음 작업

1. v1을 먼저 실행해 WSL2 Gateway를 안정화한다.
2. v2에서는 `system.run` 없이 Node 연결만 확인한다.
3. v3에서 wrapper와 approval 경계를 통과시킨 뒤에만 실행 자동화를 연다.
4. v4, v5로 브라우저를 read와 interact로 나눠 검증한다.
5. v6 Codex plan wrapper는 WSL remote read-only route로 완료 상태를 유지한다.
6. v7에서 Kiwi Voice를 마지막으로 통합한다.
7. v8에서 실제 음성 시나리오와 롤백을 검증한다.
