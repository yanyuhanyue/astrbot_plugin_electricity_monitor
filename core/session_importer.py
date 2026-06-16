"""Best-effort import of active AstrBot private and group sessions."""

from __future__ import annotations

import inspect
import time
from typing import Any

from .session_identity import (
    canonical_session_umo,
    conflicting_legacy_session_types,
    parse_session_identity,
    session_display_name,
    session_identity_key,
)
from .storage import ElectricityStore


SESSION_FIELDS = (
    "current",
    "conversations",
    "conversation_dict",
    "conversation_map",
    "conversation_cache",
    "conversation_store",
    "sessions",
    "session_map",
    "session_dict",
    "session_cache",
    "session_store",
    "cache",
)
SESSION_METHODS = (
    "get_all_conversations",
    "list_conversations",
    "get_conversations",
    "get_all_sessions",
    "list_sessions",
    "get_sessions",
    "get_all",
    "list_all",
)


class SessionImporter:
    def __init__(self, context: Any):
        self.context = context
        self.diagnostics: list[dict[str, Any]] = []
        self.legacy_repairs: set[tuple[str, str, str]] = set()

    async def collect(self) -> dict[str, Any]:
        sessions: dict[str, dict[str, str]] = {}
        self.diagnostics = []
        self.legacy_repairs = set()
        managers = self._candidate_managers()
        if not managers:
            self._record("context", "missing", "未检测到 AstrBot 会话管理器。", 0)
        for label, manager in managers:
            await self._collect_manager(label, manager, sessions)
        return {
            "sessions": sorted(
                sessions.values(),
                key=lambda item: (item["chat_type"], item["session_id"]),
            ),
            "diagnostics": self.diagnostics,
        }

    async def import_to_store(self, store: ElectricityStore) -> dict[str, Any]:
        collected = await self.collect()
        existing = {
            session_identity_key(
                item.get("chat_type"),
                item.get("session_id"),
                item.get("umo"),
            )
            for item in store.list_sessions(limit=5000)
        }
        added = 0
        now = int(time.time())
        for item in collected["sessions"]:
            key = session_identity_key(
                item["chat_type"],
                item["session_id"],
                item["umo"],
            )
            store.register_session(**item, now=now)
            if key not in existing:
                added += 1
                existing.add(key)
        repaired = 0
        for platform, chat_type, session_id in self.legacy_repairs:
            repaired += store.remove_synthetic_session(
                platform=platform,
                chat_type=chat_type,
                session_id=session_id,
            )
        return {
            "added": added,
            "discovered": len(collected["sessions"]),
            "repaired": repaired,
            "diagnostics": collected["diagnostics"],
            "sessions": collected["sessions"],
        }

    def _candidate_managers(self) -> list[tuple[str, Any]]:
        result = []
        seen = set()
        for label, owner in (
            ("context", self.context),
            ("context.provider", getattr(self.context, "provider", None)),
            ("context.core_lifecycle", getattr(self.context, "core_lifecycle", None)),
            ("context.platform_manager", getattr(self.context, "platform_manager", None)),
        ):
            if owner is None:
                continue
            for attribute in (
                "",
                "conversation_manager",
                "conversation_mgr",
                "conversation_store",
                "session_manager",
                "session_mgr",
                "session_store",
            ):
                manager = owner if not attribute else getattr(owner, attribute, None)
                if manager is None or id(manager) in seen:
                    continue
                seen.add(id(manager))
                result.append((label if not attribute else f"{label}.{attribute}", manager))
        return result

    async def _collect_manager(
        self,
        label: str,
        manager: Any,
        sessions: dict[str, dict[str, str]],
    ) -> None:
        for attribute in SESSION_FIELDS:
            if not hasattr(manager, attribute):
                continue
            before = len(sessions)
            try:
                self._collect_value(getattr(manager, attribute, None), sessions)
            except Exception as exc:
                self._record(
                    f"{label}.{attribute}",
                    "error",
                    f"读取失败：{exc}",
                    0,
                )
            else:
                self._record_source(f"{label}.{attribute}", len(sessions) - before)
        for name in SESSION_METHODS:
            method = getattr(manager, name, None)
            if not callable(method) or not self._callable_without_arguments(method):
                continue
            before = len(sessions)
            try:
                value = method()
                if inspect.isawaitable(value):
                    value = await value
                self._collect_value(value, sessions)
            except Exception as exc:
                self._record(f"{label}.{name}()", "error", f"调用失败：{exc}", 0)
            else:
                self._record_source(f"{label}.{name}()", len(sessions) - before)

    def _collect_value(
        self,
        value: Any,
        sessions: dict[str, dict[str, str]],
        *,
        label: str = "",
    ) -> None:
        if value is None:
            return
        if isinstance(value, str):
            self._add_umo(value, sessions, label=label)
            return
        if isinstance(value, dict):
            display = str(
                _first(value, ("display_name", "group_name", "nickname", "remark", "card"))
                or label
            )
            for key in (
                "unified_msg_origin",
                "umo",
                "session_id",
                "session",
                "conversation_id",
                "key",
            ):
                self._collect_value(value.get(key), sessions, label=display)
            self._add_parts(
                platform=_first(value, ("platform", "platform_id", "platform_name")),
                chat_type=_first(value, ("chat_type", "type", "message_type")),
                group_id=_first(value, ("group_id", "groupid", "room_id", "channel_id")),
                user_id=_first(value, ("user_id", "sender_id", "userid", "friend_id")),
                label=display,
                sessions=sessions,
            )
            for key, item in value.items():
                self._collect_value(str(key), sessions)
                self._collect_value(item, sessions)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._collect_value(item, sessions)
            return
        data = {
            name: getattr(value, name, None)
            for name in (
                "unified_msg_origin",
                "umo",
                "session_id",
                "session",
                "conversation_id",
                "key",
                "platform",
                "platform_id",
                "platform_name",
                "chat_type",
                "type",
                "message_type",
                "group_id",
                "groupid",
                "room_id",
                "channel_id",
                "user_id",
                "sender_id",
                "userid",
                "friend_id",
                "display_name",
                "group_name",
                "nickname",
                "remark",
                "card",
            )
        }
        self._collect_value(data, sessions)

    def _add_parts(
        self,
        *,
        platform: Any,
        chat_type: Any,
        group_id: Any,
        user_id: Any,
        label: str,
        sessions: dict[str, dict[str, str]],
    ) -> None:
        platform_text = str(platform or "").strip()
        type_text = str(chat_type or "").strip().casefold()
        if group_id:
            target = str(group_id).strip()
            message_type = "GroupMessage"
        elif user_id and (
            not type_text
            or type_text
            in {"private", "friend", "person", "user", "privatemessage", "friendmessage"}
        ):
            target = str(user_id).strip()
            message_type = "FriendMessage"
        else:
            return
        if platform_text:
            self._add_umo(
                f"{platform_text}:{message_type}:{target}",
                sessions,
                label=label,
            )

    def _add_umo(
        self,
        value: str,
        sessions: dict[str, dict[str, str]],
        *,
        label: str = "",
    ) -> None:
        text = str(value or "").strip()
        if not text or text.casefold().startswith("webchat!astrbot!"):
            return
        for repair in conflicting_legacy_session_types(text):
            self.legacy_repairs.add(
                (
                    repair["platform"],
                    repair["chat_type"],
                    repair["session_id"],
                )
            )
        parsed = parse_session_identity(text)
        if not parsed:
            return
        key = session_identity_key(
            parsed["chat_type"],
            parsed["session_id"],
            text,
        )
        item = {
            "umo": canonical_session_umo(
                text,
                platform=parsed["platform"],
                chat_type=parsed["chat_type"],
                session_id=parsed["session_id"],
            ),
            "platform": parsed["platform"],
            "chat_type": parsed["chat_type"],
            "session_id": parsed["session_id"],
            "display_name": label
            or session_display_name(parsed["chat_type"], parsed["session_id"]),
        }
        current = sessions.get(key)
        if current is None or len(item["umo"]) < len(current["umo"]):
            sessions[key] = item

    @staticmethod
    def _callable_without_arguments(method: Any) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return True
        return not any(
            parameter.default is inspect.Parameter.empty
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
            for parameter in signature.parameters.values()
        )

    def _record_source(self, source: str, count: int) -> None:
        self._record(
            source,
            "found" if count else "empty",
            "扫描到历史会话。" if count else "该来源没有可用会话。",
            count,
        )

    def _record(self, source: str, status: str, message: str, count: int) -> None:
        self.diagnostics.append(
            {
                "source": source,
                "status": status,
                "message": message,
                "count": count,
            }
        )


def _first(data: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if data.get(name):
            return data[name]
    return None
