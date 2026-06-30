"""AstrBot multi-room Yi Campus electricity monitor plugin."""

from __future__ import annotations

import inspect
import shlex
import time
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register

from .core import (
    ElectricityClient,
    ElectricityMonitorService,
    ElectricityStore,
    SessionImporter,
    decimal_text,
)
from .core.session_identity import parse_session_identity, session_display_name


PLUGIN_NAME = "astrbot_plugin_electricity_monitor"


@register(
    PLUGIN_NAME,
    "烟雨寒月",
    "监控易校园寝室剩余电量，支持多会话、多寝室和低电量提醒。",
    "1.4.0",
)
class ElectricityMonitorPlugin(Star):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ):
        super().__init__(context)
        self.config = config or {}
        self.store: ElectricityStore | None = None
        self.client: ElectricityClient | None = None
        self.service: ElectricityMonitorService | None = None
        self.session_importer = SessionImporter(context)
        self.page_api = None
        self._session_record_cache: dict[str, tuple[float, str]] = {}
        self._register_page_api()

    async def initialize(self):
        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = ElectricityStore(data_dir)
        self.client = ElectricityClient(
            data_dir,
            request_interval=float(
                self.config.get("request_interval_seconds", 2.0)
            ),
            timeout_seconds=float(
                self.config.get("request_timeout_seconds", 20.0)
            ),
        )
        await self.client.initialize()
        if (
            self.client.credential_status()["configured"]
            and self.store.auth_snapshot()["state"] == "unconfigured"
        ):
            self.store.set_auth_state("unknown")
        self.service = ElectricityMonitorService(
            self.store,
            self.client,
            self._send_text,
            scheduler_tick_seconds=int(
                self.config.get("scheduler_tick_seconds", 30)
            ),
        )
        await self.import_existing_sessions()
        if self.config.get("enabled", True):
            self.service.start()
        logger.info("[易校园电费监控] 插件已启动。")

    async def terminate(self):
        if self.service:
            await self.service.stop()
        if self.client:
            await self.client.close()

    def _register_page_api(self) -> None:
        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            logger.warning(
                "[易校园电费监控] 当前 AstrBot 不支持插件页面 API，"
                "请升级到 4.24.2 或更高版本。"
            )
            return
        try:
            from .core.page_api import PluginPageApi

            self.page_api = PluginPageApi(self)
            self.page_api.register_routes()
        except Exception as exc:
            logger.exception(f"[易校园电费监控] 注册页面 API 失败：{exc}")

    async def import_existing_sessions(self) -> dict[str, Any]:
        if not self.store:
            return {"added": 0, "discovered": 0, "diagnostics": []}
        try:
            return await self.session_importer.import_to_store(self.store)
        except Exception as exc:
            logger.warning(f"[易校园电费监控] 导入历史会话失败：{exc}")
            self.store.add_diagnostic(
                "session_import",
                f"导入历史会话失败：{exc}",
                level="warning",
            )
            return {"added": 0, "discovered": 0, "diagnostics": []}

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-1000)
    async def record_recent_session(self, event: AstrMessageEvent):
        """Record usable active-message targets."""

        if self.store:
            self._record_session(event, debounce=True)

    @filter.command("电费", alias={"dianfei"}, priority=1001)
    async def electricity_command(self, event: AstrMessageEvent):
        """查询并管理当前会话的寝室电费监控。"""

        if not self.store or not self.client or not self.service:
            yield event.plain_result("电费监控插件尚未完成初始化。")
            return
        umo = self._record_session(event) or self._umo(event)
        if not umo:
            yield event.plain_result("无法识别当前会话。")
            return
        try:
            args = shlex.split(self._command_argument(event))
            action = args[0].casefold() if args else ""
            if not action:
                message = self._list_latest(umo)
            elif action in {"查询", "查", "query"}:
                alias = " ".join(args[1:]).strip()
                report = await self.service.query_for_session(umo, alias=alias)
                message = self._query_report(report)
            elif action in {"状态", "status"}:
                message = self._status_text(umo)
            elif action in {"监控", "monitor"}:
                if not await self._can_manage(event):
                    raise ValueError(
                        "只有私聊本人、群主、群管理员或 AstrBot 管理员可以修改监控。"
                    )
                if len(args) < 3:
                    raise ValueError("用法：/电费 监控 开|关 寝室别名")
                enabled = self._switch(args[1])
                subscription = self._subscription_by_alias(umo, " ".join(args[2:]))
                self.store.update_subscription_fields(
                    subscription["id"],
                    umo=umo,
                    enabled=enabled,
                )
                message = (
                    f"{subscription['alias']} 监控已"
                    f"{'开启' if enabled else '关闭'}。"
                )
            elif action in {"阈值", "threshold"}:
                if not await self._can_manage(event):
                    raise ValueError("你没有权限修改当前会话的订阅。")
                if len(args) < 3:
                    raise ValueError("用法：/电费 阈值 寝室别名 数值")
                alias = " ".join(args[1:-1])
                threshold = decimal_text(args[-1], field="告警阈值")
                subscription = self._subscription_by_alias(umo, alias)
                self.store.update_subscription_fields(
                    subscription["id"],
                    umo=umo,
                    threshold=threshold,
                )
                message = (
                    f"{subscription['alias']} 的低电量阈值已设为 "
                    f"{threshold} {subscription['unit']}。"
                )
            elif action in {"频率", "interval"}:
                if not await self._can_manage(event):
                    raise ValueError("你没有权限修改当前会话的订阅。")
                if len(args) < 3:
                    raise ValueError("用法：/电费 频率 寝室别名 分钟")
                alias = " ".join(args[1:-1])
                minutes = int(args[-1])
                subscription = self._subscription_by_alias(umo, alias)
                self.store.update_subscription_fields(
                    subscription["id"],
                    umo=umo,
                    interval_seconds=minutes * 60,
                )
                message = f"{subscription['alias']} 的查询频率已设为 {minutes} 分钟。"
            elif action in {"帮助", "help", "?"}:
                message = self._help_text()
            else:
                message = self._help_text()
        except (ValueError, TypeError) as exc:
            message = f"操作失败：{exc}"
        except Exception as exc:
            logger.exception("[易校园电费监控] 命令执行失败")
            message = f"查询失败：{exc}"
        yield event.plain_result(message)

    async def _send_text(self, umo: str, text: str) -> bool:
        direct_result = await self._send_text_via_onebot(umo, text)
        if direct_result is not None:
            return direct_result
        result = await self.context.send_message(
            umo,
            MessageChain([Comp.Plain(text)]),
        )
        success, reason = self._send_result_success(result)
        if not success:
            logger.warning(f"[易校园电费监控] 主动消息发送返回失败：{reason}")
        return success

    async def _send_text_via_onebot(self, umo: str, text: str) -> bool | None:
        identity = parse_session_identity(umo)
        if not identity or not str(identity.get("session_id") or "").isdigit():
            return None
        platform = self._find_platform(identity["platform"])
        bot = getattr(platform, "bot", None) if platform else None
        if bot is None:
            return None
        payload = [{"type": "text", "data": {"text": text}}]
        session_id = int(identity["session_id"])
        try:
            if identity["chat_type"] == "group":
                sender = getattr(bot, "send_group_msg", None)
                if not callable(sender):
                    return None
                result = await sender(group_id=session_id, message=payload)
            else:
                sender = getattr(bot, "send_private_msg", None)
                if not callable(sender):
                    return None
                result = await sender(user_id=session_id, message=payload)
        except Exception as exc:
            logger.warning(f"[易校园电费监控] 主动消息发送异常：{exc}")
            return False
        success, reason = self._send_result_success(result)
        if not success:
            logger.warning(f"[易校园电费监控] 主动消息发送返回失败：{reason}")
        return success

    def _find_platform(self, platform_id: str):
        manager = getattr(self.context, "platform_manager", None)
        if manager is None:
            return None
        platforms = getattr(manager, "platform_insts", None)
        if platforms is None and callable(getattr(manager, "get_insts", None)):
            platforms = manager.get_insts()
        for platform in platforms or []:
            meta_getter = getattr(platform, "meta", None)
            try:
                meta = meta_getter() if callable(meta_getter) else None
            except Exception:
                meta = None
            values = {
                str(getattr(meta, "id", "") or "").casefold(),
                str(getattr(meta, "name", "") or "").casefold(),
            }
            if str(platform_id).casefold() in values:
                return platform
        return None

    @staticmethod
    def _send_result_success(result: Any) -> tuple[bool, str]:
        if result is None or result is True:
            return True, ""
        if result is False:
            return False, "返回 False"
        if isinstance(result, dict):
            status = str(result.get("status", "") or "").casefold()
            retcode = result.get("retcode")
            if retcode is not None:
                try:
                    if int(retcode) != 0:
                        return False, str(
                            result.get("wording")
                            or result.get("message")
                            or f"retcode={retcode}"
                        )
                except (TypeError, ValueError):
                    return False, f"retcode={retcode}"
            if status and status not in {"ok", "async", "success"}:
                return False, str(
                    result.get("wording") or result.get("message") or status
                )
            data = result.get("data")
            if isinstance(data, dict):
                nested_ok, nested_reason = ElectricityMonitorPlugin._send_result_success(
                    data
                )
                if not nested_ok:
                    return nested_ok, nested_reason
            return True, ""
        return bool(result), "返回空值" if not result else ""

    def _list_latest(self, umo: str) -> str:
        assert self.store
        subscriptions = self.store.list_subscriptions(umo=umo)
        if not subscriptions:
            return "当前会话尚未配置寝室。请由管理员在插件管理页添加。"
        lines = ["当前寝室电量："]
        for item in subscriptions:
            value = (
                f"{item['latest_value']} {item['unit']}"
                if item["latest_value"] is not None
                else "暂无数据"
            )
            if item.get("latest_balance"):
                value = f"{value}，余额 {item['latest_balance']}"
            state = "监控中" if item["enabled"] else "已停用"
            lines.append(
                f"- {item['alias']}：{value}（{state}，阈值 {item['threshold']}）"
            )
        lines.append("发送 /电费 查询 [别名] 可立即刷新。")
        return "\n".join(lines)

    def _status_text(self, umo: str) -> str:
        assert self.store
        subscriptions = self.store.list_subscriptions(umo=umo)
        auth = self.store.auth_snapshot()
        state_labels = {
            "valid": "有效",
            "expired": "已过期，监控暂停",
            "unknown": "待验证",
            "unconfigured": "未配置",
        }
        lines = [
            f"登录态：{state_labels.get(auth['state'], auth['state'])}",
            f"寝室订阅：{len(subscriptions)} 个",
        ]
        for item in subscriptions:
            latest = (
                f"{item['latest_value']} {item['unit']}"
                if item["latest_value"] is not None
                else "暂无数据"
            )
            if item.get("latest_balance"):
                latest = f"{latest}，余额 {item['latest_balance']}"
            lines.append(
                f"- {item['alias']}：{latest}，"
                f"{item['interval_seconds'] // 60} 分钟，"
                f"{'开启' if item['enabled'] else '关闭'}"
            )
            if item["last_error"]:
                lines.append(f"  最近错误：{item['last_error']}")
        return "\n".join(lines)

    @staticmethod
    def _query_report(report: dict[str, Any]) -> str:
        lines = []
        for item in report.get("items", []):
            balance = f"余额：{item['balance']}\n" if item.get("balance") else ""
            lines.append(
                f"{item['alias']}：{item['value']} {item['unit']}\n"
                f"{balance}"
                f"寝室：{item['room_name']}\n"
                f"更新时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['captured_at']))}"
            )
        if report.get("errors"):
            lines.append("错误：" + "；".join(report["errors"]))
        return "\n\n".join(lines) or "本次没有取得电量数据。"

    def _subscription_by_alias(
        self,
        umo: str,
        alias: str,
    ) -> dict[str, Any]:
        assert self.store
        subscription = self.store.find_subscription(umo, alias)
        if not subscription:
            raise ValueError(f"未找到别名为“{alias}”的寝室订阅。")
        return subscription

    async def _can_manage(self, event: AstrMessageEvent) -> bool:
        if event.is_private_chat() or event.is_admin():
            return True
        user_id = self._sender_id(event)
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw, dict):
            sender = raw.get("sender")
            if isinstance(sender, dict) and sender.get("role") in {"owner", "admin"}:
                return True
        group = getattr(getattr(event, "message_obj", None), "group", None)
        if self._is_group_manager(user_id, group):
            return True
        getter = getattr(event, "get_group", None)
        if callable(getter):
            try:
                group = getter()
                if inspect.isawaitable(group):
                    group = await group
                return self._is_group_manager(user_id, group)
            except Exception:
                return False
        return False

    @staticmethod
    def _is_group_manager(user_id: str, group: Any) -> bool:
        if group is None:
            return False
        owner = str(getattr(group, "group_owner", "") or "")
        admins = {
            str(item)
            for item in (getattr(group, "group_admins", None) or [])
        }
        return user_id == owner or user_id in admins

    def _record_session(
        self,
        event: AstrMessageEvent,
        *,
        debounce: bool = False,
    ) -> str:
        assert self.store
        umo = self._umo(event)
        if not umo:
            return ""
        now = time.time()
        cached = self._session_record_cache.get(umo)
        if debounce and cached and now - cached[0] < 60:
            return cached[1]
        private = bool(event.is_private_chat())
        chat_type = "private" if private else "group"
        session_id = self._sender_id(event) if private else self._group_id(event)
        display_name = (
            event.get_sender_name() if private else self._group_name(event)
        )
        if not str(display_name or "").strip():
            display_name = session_display_name(chat_type, session_id)
        recorded = self.store.register_session(
            umo=umo,
            platform=self._platform(event),
            chat_type=chat_type,
            session_id=session_id,
            display_name=str(display_name),
        ) or ""
        if recorded:
            self._session_record_cache[umo] = (now, recorded)
        return recorded

    @staticmethod
    def _command_argument(event: AstrMessageEvent) -> str:
        text = str(event.get_message_str() or "").strip()
        if text.startswith("/"):
            text = text[1:].lstrip()
        for name in ("电费", "dianfei"):
            if text == name:
                return ""
            if text.startswith(name + " "):
                return text[len(name) :].strip()
        return ""

    @staticmethod
    def _help_text() -> str:
        return (
            "电费监控命令：\n"
            "/电费\n"
            "/电费 查询 [寝室别名]\n"
            "/电费 状态\n"
            "/电费 监控 开|关 寝室别名\n"
            "/电费 阈值 寝室别名 数值\n"
            "/电费 频率 寝室别名 分钟\n"
            "寝室绑定和登录态请在插件管理页配置。"
        )

    @staticmethod
    def _switch(value: str) -> bool:
        normalized = str(value or "").strip().casefold()
        if normalized in {"开", "开启", "on", "true", "1"}:
            return True
        if normalized in {"关", "关闭", "off", "false", "0"}:
            return False
        raise ValueError("开关值应为“开”或“关”。")

    @staticmethod
    def _umo(event: AstrMessageEvent) -> str:
        value = getattr(event, "unified_msg_origin", "")
        if callable(value):
            try:
                value = value()
            except Exception:
                value = ""
        return str(value or "")

    @staticmethod
    def _platform(event: AstrMessageEvent) -> str:
        for name in ("get_platform_id", "get_platform_name"):
            getter = getattr(event, name, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        return str(value)
                except Exception:
                    pass
        return ""

    @staticmethod
    def _sender_id(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    @staticmethod
    def _group_id(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_group_id() or "")
        except Exception:
            return ""

    @staticmethod
    def _group_name(event: AstrMessageEvent) -> str:
        for name in ("get_group_name",):
            getter = getattr(event, name, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        return str(value)
                except Exception:
                    pass
        return ""
