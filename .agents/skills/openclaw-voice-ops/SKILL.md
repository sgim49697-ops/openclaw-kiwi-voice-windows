---
name: openclaw-voice-ops
description: Use for the local Windows and WSL2 OpenClaw + Kiwi Voice automation project. Covers confirmation-first voice control, Windows Node central dispatcher wrappers, browser read/click/type/screenshot permissions, VS Code/Codex read-only plan mode, Taskfile recipes, and safety policy changes.
---

# OpenClaw Voice Ops

Use this skill when working on this repository's OpenClaw + Kiwi Voice local automation setup.

## Core Workflow

1. Treat Kiwi Voice as the wake/STT/speaker input layer, not as the executor.
2. Send every STT transcript to the Codex OAuth voice planner first.
3. Let the Codex planner classify cancel, deny, clarify, approval, risk, lane, and action.
4. Require explicit user confirmation before execution.
5. Route approved work through one of three lanes:
   - Browser lane: page read, snapshot, screenshot, click, type, fill, select.
   - Windows wrapper lane: local OS or app actions through the central dispatcher.
   - Codex plan lane: VS Code + Codex read-only planning.
6. Stop before high-impact actions and request fresh confirmation.

Do not add deterministic pre-guards that intercept cancel, critical, payment, password, email,
delete, or raw-shell phrases before the Codex planner. Those phrases still go to the planner.
Safety enforcement happens after planning through schema validation, action allowlists,
approval checks, browser permissions, and dispatcher policy.

## Current Canonical State

The current canonical milestone is v7.8 complete.

Completed flow:

1. Kiwi STT produces a transcript.
2. Codex OAuth voice planner creates a dry-run approval preview.
3. The approval queue receives a pending request.
4. Telegram approval moves the request to approved or rejected.
5. Approved `notify` requests can run live once through the dispatcher.
6. Approved `browser_read` requests can run live once through `windows-cdp`.

Live `browser_read` is limited to the dedicated `windows-cdp` profile and allowlisted
read-only URLs. Gateway approvals must remain `allowlist + ask=always +
askFallback=deny + autoAllowSkills=off`.

## Do Not Re-run By Default

- Do not restart v7.2.1 through v7.2.14 microphone/STT debugging by default; v7.2.15
  and v7.8 supersede those details.
- Do not make owner voice registration a required gate. Keep it optional/later.
- Do not revisit Gateway v4 compatibility, STT tuning, or microphone calibration unless
  there is a regression.
- Do not commit Telegram tokens, chat ids, local credentials, or other personal secrets.

## Next Required Gates

1. Voice to Telegram approved Codex read-only plan.
2. Voice to approved browser interact fixture.
3. Operational runbook and stability cleanup.

Treat browser read allowlist templating as supporting cleanup, not the main next gate.

## Windows Execution Rule

Do not invent raw shell commands for runtime voice actions.

Use only:

```text
C:\OpenClawActions\Invoke-OpenClawAction.ps1 -RequestJsonBase64 <request>
```

The dispatcher validates action enum, params, paths, URLs, risk tier, and approval method.

Allowed dispatcher actions:

- `notify`
- `open_url_readonly`
- `open_vscode_codex_plan`
- `open_app_allowlisted`
- `run_task_recipe`

## Browser Permissions

Allowed after task-scoped browser approval:

- open, focus, and close tabs
- read page snapshot
- take screenshots
- click links or non-sensitive controls
- type, fill, press, select, hover, and scroll

Fresh explicit confirmation is required for:

- logged-in sensitive account changes
- submit, send, post, delete, purchase, book, cancel
- file upload or download
- password, OTP, credential, camera, microphone, or location access

Use the isolated `openclaw` browser profile by default. Do not use a logged-in user profile unless the owner explicitly asks and is present.

## Codex Rule

Voice planning uses the user's local Codex OAuth session in read-only mode.

Use:

```text
/home/user/.npm-global/bin/codex exec -C <project> --sandbox read-only --output-schema schemas/voice-planner-output.schema.json "<voice planner prompt>"
```

VS Code/Codex action requests still start as read-only plans.

Do not use `danger-full-access` or `dangerously-bypass-approvals-and-sandbox`.

## Preferred Local Commands

Use Taskfile recipes before adding ad hoc commands:

- `task check`
- `task policy:validate`
- `task eval:intents`
- `task test:browser`
- `task browser:doctor`
- `task openclaw:doctor`

Superpowers, MCP servers, Phoenix, LiteLLM, and gskill are coding or observability aids only. Do not route runtime voice commands through them.
