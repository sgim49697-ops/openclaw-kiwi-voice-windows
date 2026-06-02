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
| v7 | Kiwi Voice 통합 | Windows native Kiwi, speaker ID, Telegram approval | 음성 호출이 planner 승인 흐름까지 도달 |
| v8 | 운영 안정화 | 로그/백업/롤백/정책 문서화 | end-to-end smoke test와 롤백 절차 확인 |

## 현재 진행 상태 - 2026-06-02

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
```

현재 Node는 `system.run`, `system.run.prepare`, `system.which`, `screen.snapshot`, `camera.list`,
`location.get`, `browser.proxy` 같은 command를 선언한다. 개인 PC라 즉시 외부 노출 사고로
보지는 않지만, 이후 voice/agent/browser 자동화와 결합되면 실행 범위가 넓어질 수 있다.

따라서 v4 Browser read와 v5 Browser interact는 Windows dedicated Chrome remote CDP fallback으로 통과 상태로 본다.
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
- 허용 프로젝트 루트 제한
- `code -r`로 VS Code 열기
- Codex CLI를 read-only sandbox와 approval on-request로 실행
- Codex CLI 버전에 맞게 옵션 확인

### 검증

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\OpenClawActions\Invoke-OpenClawAction.ps1 `
  -RequestJsonBase64 "<open_vscode_codex_plan request>"
```

### 완료 조건

- 허용 루트 밖의 프로젝트 경로는 거부된다.
- Codex가 파일을 수정하지 않고 계획만 작성한다.
- workspace-write 전환은 별도 승인 절차로만 가능하다.

## v7 - Kiwi Voice 통합

### 목표

Windows native Kiwi Voice를 붙여 “호출어 감지 → 계획 요약 → 승인 요청”까지 연결한다.

### 작업

- Windows Python 환경에서 `uv venv`로 가상환경 생성
- `where.exe python`으로 interpreter 위치 확인
- `uv pip install -r requirements.txt`로 의존성 설치
- PyTorch가 필요하면 cu128 index URL을 명시
- `config.yaml`에 한국어, faster-whisper, text wake word 설정
- speaker ID owner 등록
- Telegram approval 설정
- Kiwi device를 OpenClaw Gateway에 승인

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

음성 테스트:

```text
오픈클로, 응답 테스트
오픈클로, 내 목소리 기억해
오픈클로, 누가 말하고 있어?
```

### 완료 조건

- 호출어가 오탐/미탐 없이 실사용 가능한 수준으로 동작한다.
- owner 음성 등록이 완료된다.
- 위험 명령은 Telegram 또는 음성 승인 없이는 실행되지 않는다.
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
5. v6에서 Codex plan wrapper를 붙인다.
6. v7에서 Kiwi Voice를 마지막으로 통합한다.
7. v8에서 실제 음성 시나리오와 롤백을 검증한다.
