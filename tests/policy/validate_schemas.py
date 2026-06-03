# validate_schemas.py - lightweight JSON schema sanity checks for local policy files.
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON value must be an object")
    return data


def validate_payload_hash_schema(path: Path, data: dict) -> None:
    required = data.get("required")
    properties = data.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise ValueError(f"{path}: schema must define required and properties")

    if "payloadHash" not in required:
        raise ValueError(f"{path}: payloadHash must be required")

    payload_hash = properties.get("payloadHash")
    if not isinstance(payload_hash, dict):
        raise ValueError(f"{path}: payloadHash property is required")

    if payload_hash.get("pattern") != r"^sha256:[a-f0-9]{64}$":
        raise ValueError(f"{path}: payloadHash pattern must require sha256 hex")


def validate_voice_planner_schema(path: Path, data: dict) -> None:
    required = data.get("required")
    properties = data.get("properties")
    if not isinstance(required, list) or not isinstance(properties, dict):
        raise ValueError(f"{path}: schema must define required and properties")

    required_fields = {
        "version",
        "transcript",
        "normalizedIntent",
        "decision",
        "lane",
        "riskTier",
        "action",
        "params",
        "reason",
        "userFacingPlan",
        "approvalRequired",
        "wouldExecute",
    }
    missing = sorted(required_fields.difference(required))
    if missing:
        raise ValueError(f"{path}: missing required planner fields: {', '.join(missing)}")

    decision = properties.get("decision", {})
    if decision.get("enum") != ["cancel", "deny", "clarify", "request_approval"]:
        raise ValueError(f"{path}: decision enum must match planner contract")

    lane = properties.get("lane", {})
    if lane.get("enum") != ["none", "windows_wrapper", "browser_read", "browser_interact", "codex_plan"]:
        raise ValueError(f"{path}: lane enum must match planner contract")

    would_execute = properties.get("wouldExecute", {})
    if would_execute.get("const") is not False:
        raise ValueError(f"{path}: wouldExecute must be const false for dry-run planner output")


def validate_schema(path: Path, data: dict) -> None:
    if path.name == "voice-planner-output.schema.json":
        validate_voice_planner_schema(path, data)
        return
    validate_payload_hash_schema(path, data)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: validate_schemas.py <schema.json> [<schema.json> ...]", file=sys.stderr)
        return 2

    for raw_path in argv[1:]:
        path = Path(raw_path)
        validate_schema(path, load_json(path))

    print(f"Validated {len(argv) - 1} schema file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
