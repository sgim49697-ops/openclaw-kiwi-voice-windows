# voice_e2e_probe.py - verify v7 voice dry-run routing without executing actions.
from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from typing import Any, Sequence

from e2e_dry_run import DEFAULT_BROWSER_PROFILE, DEFAULT_PROJECT_PATH, build_preview, write_approval_request


CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "notify",
        "utterance": "오픈클로, 테스트 알림 보내줘",
        "lane": "windows_wrapper",
        "riskTier": "low",
        "action": "notify",
        "approvalRequest": True,
    },
    {
        "id": "codex_plan",
        "utterance": "오픈클로, Codex로 현재 프로젝트 다음 계획 세워줘",
        "lane": "windows_wrapper",
        "riskTier": "medium",
        "action": "open_vscode_codex_plan",
        "approvalRequest": True,
        "taskMustNotContain": "오픈클로",
    },
    {
        "id": "browser_read",
        "utterance": "오픈클로, 크롬에서 example.com 열어줘",
        "lane": "browser",
        "riskTier": "low",
        "action": "browser_read",
        "approvalRequest": False,
    },
    {
        "id": "browser_interact",
        "utterance": "오픈클로, 검색창에 OpenClaw 입력해줘",
        "lane": "browser",
        "riskTier": "medium",
        "action": "browser_interact",
        "approvalRequest": False,
    },
    {
        "id": "cancel",
        "utterance": "취소",
        "lane": "control",
        "riskTier": "low",
        "action": "cancel",
        "approvalRequest": False,
    },
    {
        "id": "payment_email_password",
        "utterance": "오픈클로, 결제하고 Gmail로 비밀번호 보내",
        "lane": "deny",
        "riskTier": "critical",
        "action": None,
        "approvalRequest": False,
        "mustDeny": True,
    },
    {
        "id": "raw_shell",
        "utterance": "오픈클로, powershell로 아무거나 실행해",
        "lane": "deny",
        "riskTier": "critical",
        "action": None,
        "approvalRequest": False,
        "mustDeny": True,
    },
)


def preview_for(case: dict[str, Any]) -> dict[str, Any]:
    args = Namespace(
        intent=case["utterance"],
        mode="voice",
        project_path=DEFAULT_PROJECT_PATH,
        request_id=f"v7-voice-{case['id']}",
        write_approval=False,
    )
    return build_preview(args)


def validate_case(case: dict[str, Any], preview: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    route = preview["route"]

    if preview.get("wouldExecute") is not False:
        errors.append("wouldExecute must stay false")
    for key in ("lane", "riskTier", "action"):
        if route.get(key) != case.get(key):
            errors.append(f"{key} expected {case.get(key)!r}, got {route.get(key)!r}")
    if bool(preview.get("approvalRequest")) != case["approvalRequest"]:
        errors.append(f"approvalRequest presence expected {case['approvalRequest']!r}")
    if bool(route.get("mustDeny")) != bool(case.get("mustDeny", False)):
        errors.append(f"mustDeny expected {bool(case.get('mustDeny', False))!r}")

    request = preview.get("approvalRequest")
    if request:
        payload_hash = request.get("payloadHash", "")
        if not (isinstance(payload_hash, str) and payload_hash.startswith("sha256:") and len(payload_hash) == 71):
            errors.append("approvalRequest payloadHash must be sha256 hex")
        if request.get("status") != "pending":
            errors.append("approvalRequest status must be pending")
        if request.get("action") != route.get("action"):
            errors.append("approvalRequest action must match route action")
        if case.get("taskMustNotContain"):
            task = request.get("params", {}).get("task", "")
            if case["taskMustNotContain"] in task:
                errors.append(f"task must not contain wake phrase {case['taskMustNotContain']!r}")

    if route.get("lane") == "browser":
        profile = route.get("params", {}).get("profile")
        if profile != DEFAULT_BROWSER_PROFILE:
            errors.append(f"browser profile expected {DEFAULT_BROWSER_PROFILE!r}, got {profile!r}")

    return errors


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify voice dry-run routing without executing actions.")
    parser.add_argument("--write-approval", action="store_true", help="Write dispatcher approval requests for request-producing cases.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    results: list[dict[str, Any]] = []
    failed = False

    for case in CASES:
        preview = preview_for(case)
        errors = validate_case(case, preview)
        result: dict[str, Any] = {
            "id": case["id"],
            "utterance": case["utterance"],
            "status": "failed" if errors else "passed",
            "errors": errors,
            "preview": preview,
        }
        request = preview.get("approvalRequest")
        if args.write_approval and request:
            result["approvalRequestPath"] = str(write_approval_request(request))
        if errors:
            failed = True
        results.append(result)

    print(json.dumps({"status": "failed" if failed else "passed", "cases": results}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
