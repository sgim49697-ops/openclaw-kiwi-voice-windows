---
name: openclaw-voice-ops
description: Use for the local Windows and WSL2 OpenClaw + Kiwi Voice automation project. Covers confirmation-first voice control, Windows Node central dispatcher wrappers, browser read/click/type/screenshot permissions, VS Code/Codex read-only plan mode, Taskfile recipes, and safety policy changes.
---

# OpenClaw Voice Ops

Use this skill when working on this repository's OpenClaw + Kiwi Voice local automation setup.

## Core Workflow

1. Treat Kiwi Voice as the input and approval layer, not as the executor.
2. Produce a short plan before any voice-triggered execution.
3. Require explicit user confirmation before execution.
4. Route work through one of two lanes:
   - Browser lane: page read, snapshot, screenshot, click, type, fill, select.
   - Windows wrapper lane: local OS or app actions through the central dispatcher.
5. Stop before high-impact actions and request fresh confirmation.

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

VS Code/Codex requests start in read-only plan mode.

Use:

```text
codex -C <project> --sandbox read-only --ask-for-approval on-request "<plan prompt>"
```

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
