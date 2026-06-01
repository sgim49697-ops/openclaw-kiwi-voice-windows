# e2e_dry_run.py - preview intent routing and approval requests without execution.
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[2]
PENDING_DIR = ROOT / ".debugloop" / "queue" / "pending"
DISPATCHER_ACTIONS = {
    "notify",
    "open_url_readonly",
    "open_vscode_codex_plan",
    "open_app_allowlisted",
    "run_task_recipe",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slug_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")


def payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def approval_request(
    *,
    request_id: str,
    source: str,
    risk_tier: str,
    reason: str,
    action: str,
    params: dict,
) -> dict:
    payload = {
        "version": 1,
        "requestId": request_id,
        "source": source,
        "riskTier": risk_tier,
        "action": action,
        "params": params,
    }
    return {
        "version": 1,
        "requestId": request_id,
        "createdAt": now_iso(),
        "source": source,
        "riskTier": risk_tier,
        "reason": reason,
        "action": action,
        "params": params,
        "payloadHash": payload_hash(payload),
        "approvalMethodsAllowed": ["telegram", "manual"],
        "status": "pending",
    }


def classify_intent(intent: str, project_path: str) -> dict:
    normalized = intent.strip()
    lowered = normalized.lower()

    critical_markers = [
        "삭제",
        "결제",
        "보내",
        "gmail",
        "password",
        "otp",
        "powershell",
        "cmd",
        "python -c",
        "node -e",
        "npm",
        "npx",
        "pnpm",
    ]
    if any(marker in lowered for marker in critical_markers):
        return {
            "lane": "deny",
            "riskTier": "critical",
            "approvalRequired": False,
            "mustDeny": True,
            "reason": "critical or destructive request must not be queued",
            "action": None,
            "params": {},
        }

    if "codex" in lowered or "코덱스" in normalized or "플랜" in normalized or "계획" in normalized:
        params = {
            "projectPath": project_path,
            "task": normalized,
        }
        return {
            "lane": "windows_wrapper",
            "riskTier": "medium",
            "approvalRequired": True,
            "mustDeny": False,
            "reason": "Open VS Code and start Codex read-only plan mode.",
            "action": "open_vscode_codex_plan",
            "params": params,
        }

    browser_markers = ("브라우저", "크롬", "검색창", "검색", "결과", "browser")
    if any(marker in normalized for marker in browser_markers) or "browser" in lowered:
        browser_action = "browser_interact" if any(word in normalized for word in ("클릭", "입력", "검색", "열어")) else "browser_read"
        return {
            "lane": "browser",
            "riskTier": "medium" if browser_action == "browser_interact" else "low",
            "approvalRequired": True,
            "mustDeny": False,
            "reason": "Browser lane dry-run only; browser approvals are not dispatcher payloads.",
            "action": browser_action,
            "params": {"utterance": normalized, "profile": "openclaw"},
        }

    return {
        "lane": "unknown",
        "riskTier": "medium",
        "approvalRequired": True,
        "mustDeny": False,
        "reason": "Intent needs manual review before routing.",
        "action": None,
        "params": {"utterance": normalized},
    }


def build_preview(args: argparse.Namespace) -> dict:
    route = classify_intent(args.intent, args.project_path)
    request_id = args.request_id or f"{slug_timestamp()}-{args.mode}-dry-run"
    preview = {
        "mode": args.mode,
        "intent": args.intent,
        "route": route,
        "wouldExecute": False,
        "approvalRequest": None,
    }

    action = route.get("action")
    if route["approvalRequired"] and action in DISPATCHER_ACTIONS and not route["mustDeny"]:
        preview["approvalRequest"] = approval_request(
            request_id=request_id,
            source=f"{args.mode}-dry-run",
            risk_tier=route["riskTier"],
            reason=route["reason"],
            action=action,
            params=route["params"],
        )

    return preview


def write_approval_request(request: dict) -> Path:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    path = PENDING_DIR / f"{request['requestId']}.json"
    if path.exists():
        raise FileExistsError(f"approval request already exists: {path.relative_to(ROOT)}")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(request, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview OpenClaw text/voice intent routing without execution.")
    parser.add_argument("--intent", required=True, help="Text intent or voice utterance to preview.")
    parser.add_argument("--mode", choices=("text", "voice"), default="text")
    parser.add_argument("--project-path", default="C:\\dev\\shop", help="Project path for Codex plan previews.")
    parser.add_argument("--request-id", help="Stable request id for approval request previews.")
    parser.add_argument("--write-approval", action="store_true", help="Write a pending dispatcher approval request.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if not args.intent.strip():
        print("INTENT is required.", file=sys.stderr)
        return 2

    preview = build_preview(args)
    if args.write_approval:
        request = preview.get("approvalRequest")
        if not request:
            print("No dispatcher approval request can be written for this dry-run.", file=sys.stderr)
            return 3
        preview["approvalRequestPath"] = str(write_approval_request(request).relative_to(ROOT))

    print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
