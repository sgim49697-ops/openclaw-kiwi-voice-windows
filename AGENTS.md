# AGENTS.md - OpenClaw Kiwi Voice Windows 작업 지침

이 저장소의 모든 파일에는 아래 지침을 적용한다.

## 라이선스 기본 규칙

- 이 저장소에는 아직 프로젝트 전체에 적용할 명시적 오픈소스 라이선스를 선택하지 않았다.
- 외부 코드, 문서, 이미지, 설정 예제, 스크립트를 추가할 때는 출처 URL과 라이선스를 확인한다.
- 라이선스가 불명확하거나 재배포 권한이 확인되지 않은 자료는 저장소에 복사하지 않는다.
- 외부 자료를 참고해 작성하더라도 긴 원문 복사 대신 요약, 링크, 자체 작성 예제를 사용한다.
- MIT, Apache-2.0, BSD 계열처럼 호환성이 높은 자료라도 기존 copyright/license notice는 제거하지 않는다.
- GPL/AGPL/LGPL, CC-BY-SA, 비상업/수정금지 조건, proprietary EULA 자료는 사용자 승인 없이 추가하지 않는다.
- 새 의존성, 도구, 모델, 바이너리, 음성/이미지/폰트 asset을 추가하면 `docs/policies/license-compliance.md`의 체크리스트를 갱신한다.
- 공개 저장소에 비밀값, 토큰, 개인 인증 정보, 유료 asset 원본을 커밋하지 않는다.

## 실행 안전 규칙

- 에이전트는 raw shell 명령을 직접 실행 경로로 만들지 않는다.
- `powershell`, `cmd`, `bash`, `python -c`, `node -e`, `npx`, `npm`, `pnpm` 같은 명령을 에이전트가 임의로 조합해 실행하지 않는다.
- Windows 실행이 필요하면 중앙 wrapper인 `C:\OpenClawActions\Invoke-OpenClawAction.ps1`의 action enum으로 표현한다.
- 새 명령을 만들기 전에 먼저 `Taskfile.yml`의 기존 recipe를 우선 사용한다.
- Taskfile에 필요한 recipe가 없으면 중앙 wrapper 또는 정책 문서에 허용 범위와 롤백 방법을 명시한 뒤 추가한다.
- 브라우저 자동화는 승인된 브라우저 프로필 안에서만 읽기, 스크린샷, 클릭, 입력, 채우기, 선택을 수행할 수 있다.
- 비밀번호, OTP, 결제, 게시, 전송, 삭제, 계정 변경, 자격 증명 접근은 수동 처리 또는 강한 사용자 확인 없이는 수행하지 않는다.
- OpenClaw exec approval을 우회하지 않는다.
- Codex `danger-full-access`를 사용하지 않는다.
- Codex `dangerously-bypass-approvals-and-sandbox`를 사용하지 않는다.
- Codex 샌드박스와 승인 절차를 우회하는 옵션이나 실행 방식을 추가하지 않는다.

## 문서 관리

- 계획 문서는 `docs/plans/` 아래에 둔다.
- 정책 문서는 `docs/policies/` 아래에 둔다.
- 실행 가능한 스크립트를 추가할 때는 안전 정책, 허용 범위, 롤백 방법을 문서화한다.
