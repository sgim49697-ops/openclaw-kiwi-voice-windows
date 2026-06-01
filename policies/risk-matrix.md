# risk-matrix.md - Voice automation risk tiers and required handling.

Use this matrix when classifying a voice intent before choosing a lane or action.

| Risk tier | Examples | Default handling |
|---|---|---|
| low | Notifications, browser page read, screenshot, search query | Owner voice or task confirmation |
| medium | VS Code open, Codex read-only plan, ordinary click/type, allowed app open | Owner voice plus explicit task summary |
| high | File modification, tests that write outputs, login session use, upload/download | Telegram or manual confirmation |
| critical | Delete, payment, message send, post, credential access, admin elevation | Deny by default or manual-only |

## Lane Defaults

- Browser lane: use `browse-read` first, then `browse-interact` only after approval.
- Windows wrapper lane: use only `Invoke-OpenClawAction.ps1` with an allowlisted action.
- Codex lane: start in read-only plan mode.

## Deny By Default

Do not auto-run requests involving password extraction, 2FA bypass, hidden recording, location/camera/microphone permission grants, payment, or irreversible deletion.
