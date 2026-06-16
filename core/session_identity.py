"""AstrBot session identity parsing and canonicalization."""

from __future__ import annotations

import re
from typing import Any


UMO_PATTERN = re.compile(
    r"(?P<platform>[^:\s]+):"
    r"(?P<type>private|friend|user|person|group|guild|"
    r"FriendMessage|PrivateMessage|GroupMessage|GuildMessage):",
    re.IGNORECASE,
)


def parse_session_identity(session_id: str) -> dict[str, str] | None:
    if not isinstance(session_id, str):
        return None
    raw = session_id.strip()
    matches = list(UMO_PATTERN.finditer(raw))
    match = matches[-1] if matches else None
    if not match:
        return None
    raw_type = match.group("type").casefold()
    chat_type = "group" if "group" in raw_type or "guild" in raw_type else "private"
    raw_target = raw[match.end() :].strip()
    target_id = _target_id(raw_target, raw)
    return {
        "platform": match.group("platform"),
        "chat_type": chat_type,
        "session_id": target_id,
        "raw_target": raw_target,
    }


def conflicting_legacy_session_types(
    session_id: str,
) -> list[dict[str, str]]:
    if not isinstance(session_id, str):
        return []
    raw = session_id.strip()
    matches = list(UMO_PATTERN.finditer(raw))
    if len(matches) < 2:
        return []
    final = matches[-1]
    final_type = _message_chat_type(final.group("type"))
    target = _target_id(raw[final.end() :].strip(), raw)
    result = []
    for match in matches[:-1]:
        chat_type = _message_chat_type(match.group("type"))
        if chat_type == final_type:
            continue
        result.append(
            {
                "platform": match.group("platform"),
                "chat_type": chat_type,
                "session_id": target,
            }
        )
    return result


def canonical_session_umo(
    umo: Any = "",
    *,
    platform: Any = "",
    chat_type: Any = "",
    session_id: Any = "",
) -> str:
    raw = str(umo or "").strip()
    parsed = parse_session_identity(raw) if raw else None
    normalized_platform = str(platform or "").strip()
    normalized_type = _normalize_chat_type(chat_type)
    target = str(session_id or "").strip()
    if parsed:
        normalized_platform = normalized_platform or parsed["platform"]
        normalized_type = normalized_type or parsed["chat_type"]
        target = target or parsed["session_id"]
        if (
            len(list(UMO_PATTERN.finditer(raw))) == 1
            and
            parsed["raw_target"] == target
            and "!" not in parsed["raw_target"]
            and normalized_platform == parsed["platform"]
            and normalized_type == parsed["chat_type"]
        ):
            return raw
    if not normalized_platform or not normalized_type or not target:
        return raw
    message_type = "GroupMessage" if normalized_type == "group" else "FriendMessage"
    return f"{normalized_platform}:{message_type}:{target}"


def session_identity_key(
    chat_type: Any = "",
    session_id: Any = "",
    umo: Any = "",
) -> str:
    normalized_type = _normalize_chat_type(chat_type)
    target = str(session_id or "").strip()
    if (not normalized_type or not target) and umo:
        parsed = parse_session_identity(str(umo))
        if parsed:
            normalized_type = normalized_type or parsed["chat_type"]
            target = target or parsed["session_id"]
    if normalized_type and target:
        return f"{normalized_type}:{target}"
    return f"umo:{str(umo or '').strip()}"


def session_display_name(chat_type: str, session_id: str) -> str:
    prefix = "群聊" if _normalize_chat_type(chat_type) == "group" else "私聊"
    return f"{prefix} {session_id}" if session_id else prefix


def _normalize_chat_type(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    return "group" if "group" in text or "guild" in text else "private"


def _message_chat_type(value: str) -> str:
    text = str(value or "").casefold()
    return "group" if "group" in text or "guild" in text else "private"


def _target_id(candidate: str, raw: str) -> str:
    values = [candidate]
    values.extend(reversed([part.strip() for part in raw.split(":") if part.strip()]))
    for value in values:
        bang_parts = [part.strip() for part in str(value or "").split("!") if part.strip()]
        if len(bang_parts) >= 2:
            return bang_parts[-1]
        if str(value or "").strip().isdigit():
            return str(value).strip()
    return str(candidate or "").strip()
