# OpenClaw Kiwi Voice Windows 계획 문서

이 디렉터리는 OpenClaw Windows Node, Kiwi Voice, 브라우저 자동화, VS Code/Codex 연동 계획 문서를 보관한다.

## 문서 목록

| 문서 | 용도 |
|---|---|
| `openclaw_kiwi_voice_windows_plan.md` | 전체 구성 계획 원문 |
| `openclaw_kiwi_voice_windows_plan.html` | 전체 구성 계획 HTML 렌더링본 |
| `openclaw_kiwi_voice_windows_versioned_plan.md` | 추천 진행 순서를 버전 단위로 나눈 실행 로드맵 |

## 진행 기준

- 전체 구축을 한 번에 진행하지 않고, 버전별 종료 조건을 통과한 뒤 다음 단계로 넘어간다.
- `system.run`, 브라우저 클릭/입력, Codex 파일 수정, git push 같은 side-effect 동작은 항상 별도 승인 경계를 둔다.
- Kiwi Voice는 마지막 통합 레이어로 보고, Gateway/Node/Browser/Codex 권한 경계가 먼저 검증된 뒤 붙인다.
