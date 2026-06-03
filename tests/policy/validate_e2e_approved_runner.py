# validate_e2e_approved_runner.py - verify approved runner live safety contracts.
# debug-autoloop: command=python3 tests/policy/validate_e2e_approved_runner.py --check
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WSL_SCRIPTS = ROOT / "scripts" / "wsl"
sys.path.insert(0, str(WSL_SCRIPTS))

from e2e_approved_runner import (  # noqa: E402
    DEFAULT_BROWSER_PROFILE,
    is_browser_read_allowed_url,
    safe_url,
    validate_live_request,
)


def assert_allowed_url_contract() -> None:
    allowed = [
        "https://example.com",
        "https://example.com/",
        "https://docs.openclaw.ai",
        "https://docs.openclaw.ai/browser/read",
        "https://docs.kiwi-voice.com",
        "https://docs.kiwi-voice.com/reference",
    ]
    denied = [
        "http://docs.openclaw.ai",
        "https://example.com/path",
        "https://example.com?query=1",
        "https://docs.openclaw.ai?query=1",
        "https://docs.openclaw.ai#fragment",
        "https://user:pass@docs.openclaw.ai",
        "https://docs.openclaw.ai:8443",
        "https://sub.docs.openclaw.ai",
        "https://openclaw.ai",
    ]

    for url in allowed:
        if not is_browser_read_allowed_url(safe_url(url)):
            raise AssertionError(f"expected allowed browser_read URL: {url}")
    for url in denied:
        try:
            normalized = safe_url(url)
        except ValueError:
            continue
        if is_browser_read_allowed_url(normalized):
            raise AssertionError(f"expected denied browser_read URL: {url}")


def live_request(url: str, request_id: str = "contract-browser-read") -> dict:
    return {
        "requestId": request_id,
        "action": "browser_read",
        "riskTier": "low",
        "approvalMethod": "telegram",
        "params": {
            "profile": DEFAULT_BROWSER_PROFILE,
            "url": url,
        },
    }


def assert_live_request_contract() -> None:
    validate_live_request(
        live_request("https://docs.openclaw.ai/browser/read"),
        confirm_request_id="contract-browser-read",
    )

    denied_cases = [
        live_request("https://docs.openclaw.ai?query=1"),
        live_request("https://example.com/path"),
        {**live_request("https://docs.openclaw.ai"), "riskTier": "medium"},
        {**live_request("https://docs.openclaw.ai"), "approvalMethod": "voice"},
        {
            **live_request("https://docs.openclaw.ai"),
            "params": {"profile": "openclaw", "url": "https://docs.openclaw.ai"},
        },
    ]
    for request in denied_cases:
        try:
            validate_live_request(request, confirm_request_id=request["requestId"])
        except ValueError:
            continue
        raise AssertionError(f"expected denied live request: {request!r}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate approved runner safety contracts.")
    parser.add_argument("--check", action="store_true", help="Run safety contract checks.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    parse_args(argv)
    assert_allowed_url_contract()
    assert_live_request_contract()
    print("Validated approved runner safety contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
