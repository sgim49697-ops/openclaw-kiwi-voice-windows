# kiwi_transcript_dry_run.py - route Kiwi STT transcript text through voice dry-run.
from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from typing import Sequence

from e2e_dry_run import DEFAULT_PROJECT_PATH, build_preview, write_approval_request


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview routing for a Kiwi STT transcript without executing actions.")
    parser.add_argument("--transcript", required=True, help="Transcript text produced by Kiwi STT.")
    parser.add_argument("--project-path", default=DEFAULT_PROJECT_PATH)
    parser.add_argument("--request-id", help="Stable request id for approval request previews.")
    parser.add_argument("--write-approval", action="store_true", help="Write a pending dispatcher approval request.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    preview_args = Namespace(
        intent=args.transcript,
        mode="voice",
        project_path=args.project_path,
        request_id=args.request_id,
        write_approval=args.write_approval,
    )
    preview = build_preview(preview_args)
    if args.write_approval:
        request = preview.get("approvalRequest")
        if not request:
            print("No dispatcher approval request can be written for this transcript.", file=sys.stderr)
            return 3
        preview["approvalRequestPath"] = str(write_approval_request(request))
    print(json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
