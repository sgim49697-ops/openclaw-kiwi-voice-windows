# OpenClaw + Kiwi Voice 자동 디버깅 루프 계획서

생성일: 2026-06-01
수정 기준: 2026-06-01 현재 repo 구현, OpenClaw exec approvals/node/browser 최신 문서 확인 결과
대상 프로젝트: `openclaw-kiwi-voice-windows`
목표: 구현 중에도 agent가 계속 상태를 확인하고 작은 수정과 검증을 반복하되, 실제 실행/배포/권한 상승은 외부 승인 신호가 있어야만 진행한다.

---

## 1. 결론

이 프로젝트의 디버깅 루프는 **완전 방치형 실행 agent**가 아니라, **자동 수정 agent + 외부 승인 큐**로 설계한다.

```text
자동 가능:
  monitor → 실패 분석 → repo-local 수정 → 테스트 → 로그/요약 → 승인 요청 생성

자동 금지:
  self-approval → system.run → Windows 배포 → 브라우저 high-impact action → 권한 완화
```

승인은 agent 내부 판단이 아니라 owner voice, Telegram, OpenClaw approval, GitHub PR review, 로컬 승인 파일 같은 **agent 밖의 신호**로만 성립한다. agent는 승인 요청을 만들고 대기할 수 있지만, 자기 요청을 자기 승인으로 처리하지 않는다.

---

## 2. 자동화 레벨

| 레벨 | 이름 | 자동 실행 | 예시 | 승인 |
|---|---|---:|---|---|
| L0 | Read-only monitor | 가능 | `git status`, Gateway/Node/Browser status | 불필요 |
| L1 | Dry-run / local check | 가능 | `task check`, policy validate, intent eval skeleton | 불필요 |
| L2 | Repo-local fix | 가능 | 문서/테스트/정책 템플릿 수정, 검증, 커밋/푸시 | 일반 git 규칙 |
| L3 | Low-risk approved action | 조건부 | dispatcher `notify`, read-only URL open | 외부 승인 필요 |
| L4 | Medium/high-risk action | 조건부 | browser click/type, Codex plan launch, app open | fresh 외부 승인 필요 |
| L5 | Critical / destructive | 자동 금지 | 삭제, 결제, 전송, credential 접근, 권한 완화 | 기본 deny |

상시 백그라운드 agent는 L0-L2까지만 수행한다. L3 이상은 승인 큐에 요청을 남기고 멈춘다.

---

## 3. 핵심 루프

```text
작은 변경 또는 환경 변화 감지
→ L0/L1 check 실행
→ 실패 레이어 분류
→ L2 범위면 repo-local 수정
→ 테스트 재실행
→ 결과를 .debugloop/runs/*.jsonl에 기록
→ L3 이상이 필요하면 approval request 생성
→ 외부 승인 확인
→ 승인 후에만 dispatcher/browser/node 작업 실행
→ 결과 artifact와 regression case 저장
```

중단 조건:

```text
- Gateway approvals가 allowlist/ask always/deny/autoAllowSkills=off에서 벗어남
- Windows Node가 의도한 node인지 식별 불가
- Browser가 isolated openclaw profile이 아님
- system.run 또는 raw shell 실행이 필요함
- approval request와 실제 실행 payload가 다름
- 민감 action이 음성 단독 승인만으로 통과하려 함
```

---

## 4. 명령 체계

현재 repo에 이미 있는 안정 명령:

```bash
task check
task policy:validate
task schema:validate
task eval:intents
task test:browser
task browser:probe
task browser:doctor
task openclaw:doctor
```

추가할 권장 명령:

```bash
task debug:monitor        # L0 상태 확인만 반복
task debug:once           # L0-L1 1회 실행
task debug:autoloop       # 현재 repo를 스캔하며 safe check/probe/dry-run 반복
task debug:auto-once      # autoloop 1회 실행
task debug:agent          # L0-L2 자동 수정 루프
task debug:agent-once     # L0-L2 자동 수정 루프 1회 실행
task approval:queue       # 승인 대기 요청 목록 출력
task approval:status      # 승인 결과 확인
task e2e:dry-run          # 실제 실행 없이 routing preview
task voice:dry-run        # 실행 없이 wake/STT/intent/approval request까지만
task browser:probe        # isolated openclaw profile read probe
```

`task --watch`는 빠른 read-only check에만 사용한다. long-running child process나 실제 실행 task에는 사용하지 않는다.

`debug:autoloop`는 repo 파일을 직접 수정하지 않는다. 현재 tracked/untracked 파일을 스캔해 정책/스키마/Python compile/dry-run/monitor/probe를 자동 구성하고, 결과를 `.debugloop/runs/autoloop.jsonl`과 `.debugloop/runs/latest-summary.md`에 기록한다. 새 스크립트는 기본적으로 compile만 수행하며, 실행은 allowlisted check 또는 `# debug-autoloop: command=...` marker가 있는 경우에만 자동 편입한다.

`debug:agent`는 autoloop 결과를 입력으로 받아 L2 repair marker가 있는 실패만 자동 수정 대상으로 처리한다. `# debug-autoloop: command=...` marker는 read-only check 전용이며, 자동 수정은 같은 파일의 check marker 실패와 `# debug-agent: repair=python3 <same-file> --repair --confirm-safe-l2` marker가 동시에 있을 때만 실행된다. 수정 허용 경로는 `docs/`, `policies/`, `schemas/`, `tests/`, `evals/`, `scripts/wsl/`로 제한한다.

---

## 5. 권장 디렉터리 구조

```text
openclaw-kiwi-voice-windows/
  Taskfile.yml
  AGENTS.md

  .debugloop/
    queue/
      pending/
      approved/
      rejected/
    runs/
      latest.jsonl
      latest-summary.md
    artifacts/
      browser/
      node/
      e2e/

  .agents/
    skills/
      openclaw-voice-ops/
        SKILL.md

  policies/
    openclaw.exec-approvals.template.json
    windows-node.exec-policy.template.json
    browser-permissions.md
    risk-matrix.md

  scripts/
    win/
      Invoke-OpenClawAction.ps1
    wsl/
      check-openclaw.sh
      check-browser.sh
      check-nodes.sh

  evals/
    promptfooconfig.yaml
    voice-intents.yaml
    dangerous-commands.yaml

  tests/
    browser/
    policy/

  schemas/
    approval-request.schema.json
    openclaw-action-request.schema.json

  docs/plans/
```

`.debugloop/runs`와 `.debugloop/artifacts`는 기본적으로 git 추적하지 않는다. regression으로 승격할 케이스만 `evals/` 또는 `tests/`에 명시적으로 옮긴다.

---

## 6. Approval Queue

agent가 L3 이상 작업을 하려면 먼저 approval request를 만든다.

```json
{
  "version": 1,
  "requestId": "20260601-voice-001",
  "createdAt": "2026-06-01T12:00:00+09:00",
  "source": "debug-agent",
  "riskTier": "medium",
  "reason": "Open VS Code and start Codex read-only plan mode.",
  "action": "open_vscode_codex_plan",
  "params": {
    "projectPath": "C:\\dev\\shop",
    "task": "결제 오류 수정 계획을 세운다."
  },
  "payloadHash": "sha256:c929f5ca6dcf091c9f6d6b10aa9a02884377147331503a58fe7ca644e0feec79",
  "approvalMethodsAllowed": ["telegram", "manual"],
  "status": "pending"
}
```

`payloadHash`는 dispatcher로 보낼 실행 payload의 핵심 필드에 대한 SHA-256이다. 해시 대상은 `version`, `requestId`, `source`, `riskTier`, `action`, `params`이며, JSON key를 재귀적으로 정렬하고 공백 없는 UTF-8 JSON으로 직렬화한 값을 사용한다.

승인 완료 후 dispatcher로 보내는 payload는 현재 구현과 동일하게 base64url JSON만 사용한다. 단, 실행 직전 dispatcher는 동일한 방식으로 `payloadHash`를 재계산하고, 승인 요청 당시의 hash와 다르면 실행을 거부한다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File C:\OpenClawActions\Invoke-OpenClawAction.ps1 `
  -RequestJsonBase64 "<base64url-json>"
```

request JSON은 아래 필드를 포함해야 한다.

```json
{
  "version": 1,
  "requestId": "20260601-voice-001",
  "source": "debug-agent",
  "approvedByUser": true,
  "approvalMethod": "telegram",
  "riskTier": "medium",
  "action": "open_vscode_codex_plan",
  "params": {
    "projectPath": "C:\\dev\\shop",
    "task": "결제 오류 수정 계획을 세운다."
  },
  "payloadHash": "sha256:c929f5ca6dcf091c9f6d6b10aa9a02884377147331503a58fe7ca644e0feec79"
}
```

승인 파일을 agent가 직접 `approved`로 이동하거나 `approvedByUser=true`를 생성하는 것은 금지한다. 승인 상태 변경은 외부 승인 handler만 수행한다.
승인 handler는 pending request의 `payloadHash`를 변경할 수 없고, dispatcher payload의 action/params/requestId/riskTier/source/version이 pending request와 같은지 확인한 뒤에만 승인 payload를 생성한다.

---

## 7. Loop 0 — Monitor / Doctor

목표: 상태만 계속 확인하고 수정하지 않는다.

확인 항목:

```text
- repo clean/dirty 상태
- Gateway reachable
- exec approvals: allowlist + ask always + askFallback deny + autoAllowSkills off
- Windows Node pending/paired/connected 상태
- Browser status
- Taskfile 실행 가능 여부
```

권장 명령:

```bash
task debug:monitor
```

허용 출력:

```text
ok | warning | blocked
blocked reason
next manual action
```

현재 기준 상태:

```text
- Windows Node: Known/Paired/Connected = 1/1/1
- Windows Node safe smoke: device.status, system.notify, screen.snapshot 성공
- Windows Node pending: []
- 다음 안전 gate: system.run 사용 전 dispatcher-only local exec-policy 적용
- Browser: windows-cdp profile read + click/type/fill/select interact OK
```

---

## 8. Loop 1 — Policy

목표: 위험한 실행이 중앙 dispatcher 밖으로 빠져나가지 못하게 한다.

검증 대상:

```text
policies/openclaw.exec-approvals.template.json
policies/windows-node.exec-policy.template.json
scripts/win/Invoke-OpenClawAction.ps1
~/.openclaw/exec-approvals.json
Windows Node local exec approvals
```

통과 기준:

```text
- Gateway approvals: security=allowlist
- Gateway approvals: ask=always
- Gateway approvals: askFallback=deny
- Gateway approvals: autoAllowSkills=false
- Windows Node policy: dispatcher command only
- raw powershell/cmd/python -c/node -e/npm/npx deny
```

정책 루프는 `security=full`, `ask=off`, `autoAllowSkills=on`을 자동으로 만들 수 없다. 이런 변경이 필요하다는 판단이 나와도 approval queue에 올리지 않고 `critical denied`로 기록한다.

---

## 9. Loop 2 — Intent / Eval

목표: 음성 명령이 올바른 intent, risk tier, execution lane으로 분류되는지 확인한다.

필수 규칙:

```text
- 모든 실행 intent는 requires_confirmation=true
- destructive command는 must_deny=true
- browser lane과 OS wrapper lane을 혼동하지 않음
- Codex plan은 read-only plan으로만 분류
- "실행하지 마"는 cancel/deny로 분류
```

권장 명령:

```bash
task eval:intents
task e2e:dry-run INTENT="크롬에서 OpenClaw Windows Node 찾아서 보여줘"
```

Promptfoo는 반복 regression에 적합하다. 최신 문서 기준으로 promptfoo는 LLM 앱 평가와 red teaming용 CLI/library이므로, 위험 명령과 오인식 케이스를 dataset으로 쌓는다.

---

## 10. Loop 3 — Wrapper Dispatcher

목표: Windows 실행은 중앙 dispatcher 하나로 통제한다.

현재 허용 action:

```text
notify
open_url_readonly
open_vscode_codex_plan
open_app_allowlisted
run_task_recipe
```

현재 금지 action:

```text
run_shell
run_powershell_command
run_cmd_command
run_python_inline
run_node_inline
delete_file
git_push
send_email
post_social
make_payment
export_credentials
open_password_manager
```

dry-run은 dispatcher 자체에 없는 임의 `-DryRun` 인자를 만들지 않는다. 대신 test harness가 request JSON을 만들고 “would call dispatcher” 형태로 preview한다.

통과 기준:

```text
- PowerShell parser OK
- 허용 action enum만 통과
- approvedByUser=true 없으면 실패
- approvalMethod가 voice/telegram/manual 중 하나가 아니면 실패
- payloadHash가 실행 payload와 다르면 실패
- URL은 http/https만 허용
- path는 allowed root 안만 허용
- wrapper 로그 JSONL 생성
```

---

## 11. Loop 4 — Browser

목표: isolated `openclaw` profile에서 read → interact 순서로 검증한다.

현재 진단:

```text
local openclaw before reset → raw CDP page commands timeout, rendererCount 0
local openclaw after reset → raw CDP 일시 OK, 재시작 후 rendererCount 0 / page commands timeout 재발
linux headless/noSandbox retry → wrapper timeout 유지, config restored
windows-cdp fallback → OpenClaw browser read probe OK
windows-cdp interact → local fixture click/type/fill/select OK
```

진단 probe:

```text
task browser:probe
python3 scripts/wsl/browser_probe.py
python3 scripts/wsl/browser_probe.py --profile windows-cdp --url https://example.com --snapshot-limit 200
python3 scripts/wsl/browser_interact_probe.py --profile windows-cdp
node scripts/wsl/cdp_probe.mjs --cdp-url http://127.0.0.1:18800
node scripts/wsl/cdp_probe.mjs --cdp-url http://127.0.0.1:9222 --out .debugloop/artifacts/browser/windows-cdp-probe.json
```

probe는 `status`, `tabs`, `open`, `doctor --deep`, `snapshot`, `screenshot`, `console`, `errors`를 순서대로 실행하고, 실패 단계와 Gateway 로그 excerpt를 `.debugloop/runs/latest.jsonl` 및 `.debugloop/artifacts/browser/`에 기록한다.
CDP probe는 `/json/version`, `/json/list`, `Browser.getVersion`, direct page WebSocket, Browser `Target.attachToTarget`, `Runtime.evaluate`, `Page.captureScreenshot`, `Accessibility.getFullAXTree`를 직접 호출하고 `.debugloop/artifacts/browser/cdp-probe.json`에 결과를 남긴다.
Interact probe는 repo-local fixture를 `127.0.0.1:<free-port>`에 띄우고 `windows-cdp` profile에서 `click`, `type`, `fill`, `select` 후 fresh snapshot으로 상태 변화를 확인한다.
같은 `windows-cdp` profile의 browser probes는 병렬 실행하지 않는다. OpenClaw browser refs는 최신 snapshot에 묶이므로 read/interact 검증은 순차 실행한다.

현재 판정:

```text
local managed openclaw profile은 renderer/CDP page command가 불안정하며 rendererCount 0 상태가 재발한다.
manual profile reset 직후 raw CDP는 일시 복구됐지만 OpenClaw wrapper connectOverCDP timeout은 계속된다.
Windows dedicated Chrome CDP profile windows-cdp는 raw CDP, OpenClaw wrapper read probe, interact probe가 모두 통과한다.
기본 Browser lane은 windows-cdp를 사용하고, 개인 user profile은 사용하지 않는다.
localhost fixture 접근은 `browser.ssrfPolicy.allowedHostnames`의 `127.0.0.1`, `localhost`로 제한한다.
`user` profile을 지정한 interact probe는 startup 단계에서 blocked 처리된다.
```

진행 순서:

```text
1. browser plugin/config 활성 여부 확인
2. Gateway restart 후 browser.request 재확인
3. raw CDP probe로 browser endpoint와 page command를 분리
4. profile reset 또는 Chromium/headless/CDP 실행 옵션 점검
5. windows-cdp fallback으로 browse-read 검증
6. windows-cdp fixture로 browse-interact 검증
7. browser-high-impact: upload/download/submit/send/payment은 별도 승인 또는 deny
```

브라우저 루프는 dedicated `windows-cdp` profile을 기본으로 사용한다. `user` profile은 사용자가 현장에 있고 명시적으로 요청한 경우에만 approval queue에 올린다.

---

## 12. Loop 5 — Codex Plan Mode

목표: VS Code + Codex 작업은 항상 read-only plan으로 시작한다.

허용 흐름:

```text
voice/text intent
→ plan 생성
→ approval request 생성
→ 외부 승인
→ dispatcher action: open_vscode_codex_plan
→ VS Code WSL remote로 project 열기
→ WSL Codex: codex -C <project> --sandbox read-only --ask-for-approval on-request
```

현재 기본 project root는 `\\wsl.localhost\Ubuntu-22.04\home\user\projects\openclaw-kiwi-voice-windows`다.
Windows Git은 이 UNC repo를 dubious ownership으로 거부하므로 Windows Codex를 UNC에 직접 붙이지 않는다.
dispatcher는 WSL UNC path를 감지하면 `wsl.exe -d Ubuntu-22.04 --cd <project>` 안에서 `/home/user/.npm-global/bin/codex`를 실행한다.

통과 기준:

```text
- Codex가 read-only sandbox로 시작
- 파일 수정 없음
- 명령 실행 없음
- plan 결과만 생성
- workspace-write 전환은 별도 승인 필요
- C:\Windows 같은 allowed root 밖 path 거부
- payloadHash mismatch와 missing approval 거부
```

2026-06-02 v6 smoke:

```text
deployed dispatcher positive → ok
outside-root / payloadHash mismatch / missing approval → denied
Gateway approvals locked 유지
```

---

## 13. Loop 6 — Gateway / Windows Node

목표: Gateway와 Windows Node 연결, pairing, node policy, exec approvals를 실제로 확인한다.

진행 순서:

```text
1. Gateway reachable 확인
2. approvals locked 확인
3. Windows Node/Tray pairing
4. openclaw nodes pending → approve
5. nodes describe로 declared commands 확인
6. device.status / system.notify / screen.snapshot만 smoke test
7. system.run은 dispatcher-only policy 배포 전까지 테스트 금지
```

OpenClaw node 문서 기준으로 node command는 node가 선언한 command와 gateway platform policy 두 gate를 통과해야 한다. camera, location, screen.record 같은 privacy-heavy command는 opt-in 전까지 금지한다.

현재 기준으로 Windows Node 연결과 safe smoke test는 통과했다. 다음 루프 작업은
`system.run` 실기 테스트가 아니라, Node local exec-policy를 dispatcher-only로 잠그는 것이다.
Node가 `system.run`, `camera.list`, `location.get`, `screen.snapshot`, `browser.proxy`를 선언하더라도
자동화 루프는 dispatcher 정책 잠금 전까지 `system.run`, camera, location, screen.record를 호출하지 않는다.

---

## 14. Loop 7 — Kiwi Voice Dry-run

목표: 마이크/STT/호출어/목소리 인식/승인 문구가 실제 실행 없이 동작하는지 확인한다.

권장 순서:

```text
1. Kiwi 실행
2. wake word 인식
3. STT 결과 로그 확인
4. speaker ID 확인
5. intent classifier dry-run
6. 실행 계획 읽기
7. "취소" / "실행" 구분 확인
8. approval request 생성까지만 확인
```

위험 action은 음성 단독 승인으로 통과하지 않는다. medium 이상은 Telegram 또는 manual 승인 신호를 요구한다.

---

## 15. Loop 8 — E2E

목표: 실제 사용자 흐름을 텍스트부터 음성까지 단계적으로 연결한다.

```text
text dry-run
→ text approval request
→ approved text run
→ voice dry-run
→ voice approval request
→ approved voice run
```

통과 기준:

```text
- 실행 전 계획이 항상 표시됨
- 승인 전 system.run 없음
- 승인 request와 실행 payload가 일치함
- 결과 artifact 존재
- 실패 지점이 JSONL에 기록됨
```

---

## 16. Loop 9 — Review / Learning

목표: 반복 실패를 regression/eval로 승격한다.

```text
.debugloop/runs/latest.jsonl 읽기
→ 실패 빈도 높은 layer 추출
→ evals 또는 tests에 case 추가
→ AGENTS.md/SKILL.md 업데이트 후보 생성
→ 정책 변경 후보는 approval queue에 올리지 않고 수동 검토 목록에만 기록
```

회고 결과가 repo 파일을 바꾸는 경우 일반 git 규칙에 따라 검증, 한글 커밋, push를 수행한다.

---

## 17. JSONL 로그 표준

성공/대기 로그:

```json
{
  "timestamp": "2026-06-01T12:00:00+09:00",
  "runId": "20260601-120000-codex-plan",
  "level": "L4",
  "layer": "wrapper",
  "status": "approval_pending",
  "action": "open_vscode_codex_plan",
  "riskTier": "medium",
  "payloadHash": "sha256:c929f5ca6dcf091c9f6d6b10aa9a02884377147331503a58fe7ca644e0feec79",
  "approvalRequest": ".debugloop/queue/pending/20260601-120000-codex-plan.json",
  "result": "waiting_for_external_approval"
}
```

실패 로그:

```json
{
  "timestamp": "2026-06-01T12:05:00+09:00",
  "runId": "20260601-120500-node",
  "level": "L0",
  "layer": "node",
  "status": "blocked",
  "failedAt": "node_pairing",
  "error": "NO_CONNECTED_NODE",
  "suggestedNextCheck": "Start Windows OpenClaw Node/Tray and set Gateway URL ws://localhost:18789"
}
```

---

## 18. 구현 순서

### Batch A — 안전한 자동 monitor

```text
- .debugloop 구조 추가
- debug:once, debug:monitor task 추가
- Gateway/approvals/node/browser 상태를 JSONL로 기록
- repo 수정 없음
```

현재 구현 상태:

```text
- .debugloop queue/runs/artifacts 기본 구조 추가
- .debugloop/runs와 .debugloop/artifacts runtime 출력은 gitignore 처리
- scripts/wsl/debug_monitor.py 추가
- task debug:once, task debug:monitor 추가
- debug_monitor.py가 browser status뿐 아니라 live doctor까지 확인
- scripts/wsl/debug_autoloop.py 추가
- task debug:autoloop, task debug:auto-once 추가
```

### Batch B — repo-local 자동 수정 루프

```text
- debug:agent task 추가
- L0-L2 실패만 자동 수정 허용
- 수정 후 task check, git diff --check
- 검증 통과 시 한글 커밋
- debug agent 자체는 push하지 않음
```

현재 구현 상태:

```text
- scripts/wsl/debug_agent.py 추가
- task debug:agent, task debug:agent-once 추가
- autoloop marker를 read-only check marker로 제한
- repair marker 기반 L2 자동 수정 gate 추가
- debug_agent.py 결과를 .debugloop/runs/agent.jsonl과 latest-agent-summary.md에 기록
```

### Batch C — approval queue

```text
- approval request schema 추가
- payloadHash를 승인 요청과 dispatcher payload 양쪽에서 검증
- pending/approved/rejected directory 추가
- approval:queue/status task 추가
- agent self-approval 방지 규칙 추가
```

현재 구현 상태:

```text
- approval request schema 추가 완료
- dispatcher payloadHash 검증 추가 완료
- approval:queue, approval:status task 추가 완료
- e2e:dry-run, voice:dry-run task 추가 완료
- .debugloop/queue/*.json runtime request는 gitignore 처리
```

### Batch D — Node / dispatcher deploy

```text
- Windows Node pairing 완료 상태에서 진행
- 기존 Windows Node exec-policy timestamp backup
- C:\OpenClawActions 배포
- Windows Node dispatcher-only exec policy 적용
- Gateway approvals locked 상태 재확인
- dispatcher notify action만 approved smoke test
- payloadHash mismatch negative test
- raw powershell/cmd/python -c/node -e deny 확인
- open_vscode_codex_plan, run_task_recipe, Browser/Kiwi는 다음 batch로 보류
```

### Batch E — Browser / Voice / E2E

```text
- browser.request 문제 해결
- browse-read → browse-interact 순서 검증
- Kiwi Voice dry-run
- external approval 기반 voice approved run
```

---

## 19. Definition of Done

```text
- task check 통과
- debug:monitor가 장시간 read-only로 안정 동작
- debug:agent가 L0-L2만 자동 수정
- approval queue가 L3 이상 작업을 멈춰 세움
- dispatcher는 RequestJsonBase64 payload만 허용
- dispatcher는 payloadHash 불일치 payload를 거부
- Codex plan은 read-only로만 시작
- Windows 실행은 중앙 wrapper만 통과
- raw shell negative cases 차단
- browser는 isolated openclaw profile 기본
- 위험 action은 fresh external approval 또는 deny
- 실패 로그가 JSONL로 남음
- 반복 실패는 eval/test case로 승격됨
```

---

## 20. 참고 자료

- OpenAI Codex Automations: https://openai.com/academy/codex-automations/
- OpenAI Codex agent loop: https://openai.com/index/unrolling-the-codex-agent-loop/
- OpenAI Agent Evals: https://developers.openai.com/api/docs/guides/agent-evals
- promptfoo Intro: https://www.promptfoo.dev/docs/intro/
- Taskfile guide: https://taskfile.dev/docs/guide
- Playwright Trace Viewer: https://playwright.dev/docs/trace-viewer
- OpenClaw Exec Approvals: https://docs.openclaw.ai/tools/exec-approvals
- OpenClaw Nodes: https://docs.openclaw.ai/nodes
- OpenClaw Browser: https://docs.openclaw.ai/tools/browser
- OpenClaw Browser Control: https://docs.openclaw.ai/tools/browser-control
