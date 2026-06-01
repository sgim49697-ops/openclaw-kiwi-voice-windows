# validate_policy.py - minimal JSON policy validator for OpenClaw wrapper templates.
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


def validate_default_deny(path: Path, data: dict) -> None:
    if data.get("defaultAction") != "deny":
        raise ValueError(f"{path}: defaultAction must be deny")


def validate_windows_node_policy(path: Path, data: dict) -> None:
    validate_default_deny(path, data)
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"{path}: rules must be a non-empty list")

    patterns = [rule.get("pattern", "") for rule in rules if isinstance(rule, dict)]
    if not any("Invoke-OpenClawAction.ps1 -RequestJsonBase64 *" in pattern for pattern in patterns):
        raise ValueError(f"{path}: central dispatcher pattern is required")

    if rules[-1].get("pattern") != "*" or rules[-1].get("action") != "deny":
        raise ValueError(f"{path}: final rule must deny *")


def validate_gateway_policy(path: Path, data: dict) -> None:
    validate_default_deny(path, data)
    if data.get("ask") != "always":
        raise ValueError(f"{path}: ask must be always")
    if data.get("askFallback") != "deny":
        raise ValueError(f"{path}: askFallback must be deny")
    if data.get("autoAllowSkills") is not False:
        raise ValueError(f"{path}: autoAllowSkills must be false")


def validate(path: Path) -> None:
    data = load_json(path)
    name = path.name
    if name == "windows-node.exec-policy.template.json":
        validate_windows_node_policy(path, data)
    elif name == "openclaw.exec-approvals.template.json":
        validate_gateway_policy(path, data)
    else:
        validate_default_deny(path, data)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: validate_policy.py <policy.json> [<policy.json> ...]", file=sys.stderr)
        return 2

    for raw_path in argv[1:]:
        validate(Path(raw_path))

    print(f"Validated {len(argv) - 1} policy file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
