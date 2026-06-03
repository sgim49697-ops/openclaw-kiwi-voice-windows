# telegram_approval.py - Telegram approval adapter for pending OpenClaw requests.
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
QUEUE_ROOT = ROOT / ".debugloop" / "queue"
PENDING_DIR = QUEUE_ROOT / "pending"
APPROVED_DIR = QUEUE_ROOT / "approved"
REJECTED_DIR = QUEUE_ROOT / "rejected"
DEFAULT_ENV_PATH = ROOT / ".debugloop" / "local" / "telegram-approval.env"
DEFAULT_OFFSET_PATH = ROOT / ".debugloop" / "local" / "telegram-offset.json"
CALLBACK_PATTERN = re.compile(r"^(approve|reject):([A-Za-z0-9][A-Za-z0-9_.:-]*):([a-f0-9]{12})$")
FIXTURE_CHAT_ID = "fixture-owner"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{relative(path)}: expected JSON object")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def request_path(status: str, request_id: str) -> Path:
    return QUEUE_ROOT / status / f"{request_id}.json"


def load_pending_request(request_id: str) -> tuple[Path, dict[str, Any]]:
    path = request_path("pending", request_id)
    if not path.exists():
        raise FileNotFoundError(f"pending request not found: {relative(path)}")
    request = read_json(path)
    if request.get("requestId") != request_id:
        raise ValueError("requestId in file does not match requested id")
    if request.get("status") != "pending":
        raise ValueError("only pending requests can be handled")
    return path, request


def load_request_any_status(request_id: str) -> tuple[Path, dict[str, Any]]:
    for status in ("pending", "approved", "rejected"):
        path = request_path(status, request_id)
        if path.exists():
            return path, read_json(path)
    raise FileNotFoundError(f"approval request not found: {request_id}")


def payload_tail(request: dict[str, Any]) -> str:
    payload_hash = request.get("payloadHash")
    if not isinstance(payload_hash, str) or not payload_hash.startswith("sha256:"):
        raise ValueError("payloadHash is required")
    return payload_hash[-12:]


def callback_data(decision: str, request: dict[str, Any]) -> str:
    data = f"{decision}:{request['requestId']}:{payload_tail(request)}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data exceeds 64 bytes")
    return data


def trim_text(value: Any, limit: int = 700) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def render_request(request: dict[str, Any]) -> dict[str, Any]:
    risk = str(request.get("riskTier", "unknown"))
    action = str(request.get("action", "unknown"))
    request_id = str(request.get("requestId", "unknown"))
    params = json.dumps(request.get("params", {}), ensure_ascii=False, sort_keys=True)
    text = "\n".join(
        [
            "OpenClaw approval request",
            f"id: {request_id}",
            f"risk: {risk}",
            f"action: {action}",
            f"reason: {trim_text(request.get('reason', ''))}",
            f"params: {trim_text(params)}",
        ]
    )
    keyboard: list[list[dict[str, str]]] = []
    if risk != "critical":
        keyboard.append([{"text": "Approve", "callback_data": callback_data("approve", request)}])
    keyboard.append([{"text": "Reject", "callback_data": callback_data("reject", request)}])
    return {
        "text": text,
        "reply_markup": {"inline_keyboard": keyboard},
    }


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def telegram_config(env_file: Path) -> dict[str, str]:
    file_values = load_env_file(env_file)
    config = dict(file_values)
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_APPROVAL_WEBHOOK_SECRET"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    return config


def require_config(config: dict[str, str], *keys: str) -> None:
    missing = [key for key in keys if not config.get(key) or config.get(key, "").startswith("REPLACE_WITH_")]
    if missing:
        raise ValueError(f"missing local Telegram config: {', '.join(missing)}")


def telegram_api(token: str, method: str, payload: dict[str, Any] | None = None, timeout: int = 20) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API {method} failed: HTTP {exc.code}: {body}") from exc
    data_obj = json.loads(body)
    if not isinstance(data_obj, dict) or not data_obj.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed")
    return data_obj


def answer_callback(token: str | None, callback_id: str | None, text: str) -> None:
    if not token or not callback_id:
        return
    telegram_api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]}, timeout=10)


def chat_matches(callback_query: dict[str, Any], expected_chat_id: str) -> bool:
    candidates: list[str] = []
    message = callback_query.get("message")
    if isinstance(message, dict):
        chat = message.get("chat")
        if isinstance(chat, dict) and chat.get("id") is not None:
            candidates.append(str(chat["id"]))
    sender = callback_query.get("from")
    if isinstance(sender, dict) and sender.get("id") is not None:
        candidates.append(str(sender["id"]))
    return expected_chat_id in candidates


def existing_status(request_id: str) -> str | None:
    for status in ("approved", "rejected"):
        if request_path(status, request_id).exists():
            return status
    return None


def transition_pending(
    *,
    request_id: str,
    target_status: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    source, request = load_pending_request(request_id)
    target = request_path(target_status, request_id)
    if target.exists():
        raise FileExistsError(f"target request already exists: {relative(target)}")
    timestamp = now_iso()
    request["status"] = target_status
    request["approvalMethod"] = "telegram"
    if target_status == "approved":
        request["approvedBy"] = actor
        request["approvedAt"] = timestamp
        request["approvedReason"] = reason
    else:
        request["rejectedBy"] = actor
        request["rejectedAt"] = timestamp
        request["rejectedReason"] = reason
    write_json(target, request)
    source.unlink()
    return request


def handle_callback(
    *,
    update: dict[str, Any],
    expected_chat_id: str,
    token: str | None = None,
) -> dict[str, Any]:
    callback_query = update.get("callback_query")
    if not isinstance(callback_query, dict):
        raise ValueError("update does not contain callback_query")
    callback_id = str(callback_query.get("id", ""))
    if not chat_matches(callback_query, expected_chat_id):
        answer_callback(token, callback_id, "Unauthorized Telegram approver")
        raise PermissionError("callback chat/user does not match TELEGRAM_CHAT_ID")
    data = str(callback_query.get("data", ""))
    match = CALLBACK_PATTERN.match(data)
    if not match:
        answer_callback(token, callback_id, "Invalid approval callback")
        raise ValueError("invalid callback data")
    decision, request_id, received_tail = match.groups()
    prior_status = existing_status(request_id)
    if prior_status:
        answer_callback(token, callback_id, f"Already {prior_status}")
        return {"status": "ignored", "requestId": request_id, "existingStatus": prior_status}
    try:
        _path, request = load_pending_request(request_id)
    except FileNotFoundError:
        answer_callback(token, callback_id, "Unknown request")
        raise
    if received_tail != payload_tail(request):
        answer_callback(token, callback_id, "Request hash mismatch")
        raise ValueError("callback payloadHash tail mismatch")

    actor = f"telegram:{expected_chat_id}"
    if decision == "approve" and request.get("riskTier") == "critical":
        transition_pending(
            request_id=request_id,
            target_status="rejected",
            actor=actor,
            reason="critical requests cannot be approved through Telegram",
        )
        answer_callback(token, callback_id, "Critical request rejected")
        raise PermissionError("critical requests cannot be approved through Telegram")

    if decision == "approve":
        transition_pending(request_id=request_id, target_status="approved", actor=actor, reason="approved via Telegram")
        answer_callback(token, callback_id, "Approved")
        return {"status": "approved", "requestId": request_id, "approvalMethod": "telegram"}

    transition_pending(request_id=request_id, target_status="rejected", actor=actor, reason="rejected via Telegram")
    answer_callback(token, callback_id, "Rejected")
    return {"status": "rejected", "requestId": request_id, "approvalMethod": "telegram"}


def load_update_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        return read_json(Path(args.file))
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("handle-update requires --file or JSON on stdin")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("update JSON must be an object")
    return data


def callback_update(*, request_id: str, decision: str, tail: str, chat_id: str) -> dict[str, Any]:
    data = f"{decision}:{request_id}:{tail}"
    return {
        "update_id": 1,
        "callback_query": {
            "id": f"fixture-{request_id}",
            "from": {"id": chat_id},
            "message": {"chat": {"id": chat_id}},
            "data": data,
        },
    }


def print_json(data: dict[str, Any]) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_render(args: argparse.Namespace) -> int:
    _path, request = load_pending_request(args.request_id)
    return print_json(render_request(request))


def command_send_pending(args: argparse.Namespace) -> int:
    config = telegram_config(args.env_file)
    require_config(config, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    token = config["TELEGRAM_BOT_TOKEN"]
    telegram_api(token, "getMe", {}, timeout=10)
    _path, request = load_pending_request(args.request_id)
    rendered = render_request(request)
    payload = {
        "chat_id": config["TELEGRAM_CHAT_ID"],
        "text": rendered["text"],
        "reply_markup": rendered["reply_markup"],
    }
    response = telegram_api(token, "sendMessage", payload, timeout=20)
    result = response.get("result", {})
    return print_json(
        {
            "status": "sent",
            "requestId": args.request_id,
            "chatId": str(config["TELEGRAM_CHAT_ID"]),
            "messageId": result.get("message_id") if isinstance(result, dict) else None,
        }
    )


def load_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    data = read_json(path)
    value = data.get("offset")
    return int(value) if value is not None else None


def save_offset(path: Path, offset: int) -> None:
    write_json(path, {"offset": offset, "updatedAt": now_iso()})


def command_poll_once(args: argparse.Namespace) -> int:
    config = telegram_config(args.env_file)
    require_config(config, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    token = config["TELEGRAM_BOT_TOKEN"]
    telegram_api(token, "getMe", {}, timeout=10)
    payload: dict[str, Any] = {
        "timeout": args.timeout,
        "allowed_updates": ["callback_query"],
    }
    offset = load_offset(args.offset_file)
    if offset is not None:
        payload["offset"] = offset
    response = telegram_api(token, "getUpdates", payload, timeout=args.timeout + 10)
    updates = response.get("result", [])
    if not updates:
        return print_json({"status": "ok", "message": "no updates"})
    handled: list[dict[str, Any]] = []
    max_update_id = offset or 0
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = int(update.get("update_id", 0))
        max_update_id = max(max_update_id, update_id + 1)
        if "callback_query" not in update:
            continue
        handled.append(handle_callback(update=update, expected_chat_id=str(config["TELEGRAM_CHAT_ID"]), token=token))
        break
    save_offset(args.offset_file, max_update_id)
    return print_json({"status": "ok", "handled": handled, "nextOffset": max_update_id})


def command_handle_update(args: argparse.Namespace) -> int:
    config = telegram_config(args.env_file)
    expected_chat_id = args.chat_id or config.get("TELEGRAM_CHAT_ID")
    if not expected_chat_id:
        raise ValueError("handle-update requires --chat-id or TELEGRAM_CHAT_ID")
    token = config.get("TELEGRAM_BOT_TOKEN")
    update = load_update_from_args(args)
    return print_json(handle_callback(update=update, expected_chat_id=str(expected_chat_id), token=token))


def command_probe_fixture(args: argparse.Namespace) -> int:
    _path, request = load_request_any_status(args.request_id)
    tail = payload_tail(request)
    if args.wrong_tail:
        tail = "0" * 12
    callback_chat_id = args.callback_chat_id or args.chat_id
    update = callback_update(
        request_id=args.request_id,
        decision=args.decision,
        tail=tail,
        chat_id=str(callback_chat_id),
    )
    result = handle_callback(update=update, expected_chat_id=str(args.chat_id))
    return print_json({"status": "ok", "result": result})


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram approval adapter for OpenClaw pending requests.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser("render", help="Render one pending request as Telegram message JSON.")
    render.add_argument("--request-id", required=True)

    send_pending = subparsers.add_parser("send-pending", help="Send one pending request to Telegram.")
    send_pending.add_argument("--request-id", required=True)

    poll_once = subparsers.add_parser("poll-once", help="Poll Telegram getUpdates once and handle one callback query.")
    poll_once.add_argument("--timeout", type=int, default=30)
    poll_once.add_argument("--offset-file", type=Path, default=DEFAULT_OFFSET_PATH)

    handle_update = subparsers.add_parser("handle-update", help="Handle one Telegram update JSON from --file or stdin.")
    handle_update.add_argument("--file")
    handle_update.add_argument("--chat-id")

    fixture = subparsers.add_parser("probe-fixture", help="Handle a synthetic Telegram callback update.")
    fixture.add_argument("--request-id", required=True)
    fixture.add_argument("--decision", choices=("approve", "reject"), required=True)
    fixture.add_argument("--chat-id", default=FIXTURE_CHAT_ID)
    fixture.add_argument("--callback-chat-id")
    fixture.add_argument("--wrong-tail", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    try:
        if args.command == "render":
            return command_render(args)
        if args.command == "send-pending":
            return command_send_pending(args)
        if args.command == "poll-once":
            return command_poll_once(args)
        if args.command == "handle-update":
            return command_handle_update(args)
        if args.command == "probe-fixture":
            return command_probe_fixture(args)
    except Exception as exc:
        print(f"telegram approval error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
