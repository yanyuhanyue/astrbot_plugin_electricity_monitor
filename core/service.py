"""Polling, query deduplication and threshold alert orchestration."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from .client import AuthExpiredError, ElectricityClient
from .models import Reading, RoomRef, display_decimal
from .storage import ElectricityStore


SendCallback = Callable[[str, str], Awaitable[bool]]


class ElectricityMonitorService:
    def __init__(
        self,
        store: ElectricityStore,
        client: ElectricityClient,
        send_callback: SendCallback,
        *,
        scheduler_tick_seconds: int = 30,
    ):
        self.store = store
        self.client = client
        self.send_callback = send_callback
        self.scheduler_tick_seconds = max(5, int(scheduler_tick_seconds))
        self._task: asyncio.Task[None] | None = None
        self._scan_lock = asyncio.Lock()
        self._stopping = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._scheduler_loop(),
            name="astrbot-electricity-monitor-scheduler",
        )

    async def stop(self) -> None:
        self._stopping = True
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _scheduler_loop(self) -> None:
        while not self._stopping:
            try:
                await self.scan_due()
                self.store.remove_old_data()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.add_diagnostic(
                    "scheduler",
                    f"调度循环异常：{exc}",
                    level="error",
                )
            await asyncio.sleep(self.scheduler_tick_seconds)

    async def scan_due(self, *, now: int | None = None) -> dict[str, Any]:
        timestamp = int(now if now is not None else time.time())
        auth = self.store.auth_snapshot()
        if auth["state"] == "unconfigured":
            return {
                "queried_rooms": 0,
                "subscriptions": 0,
                "alerts": 0,
                "errors": ["尚未配置易校园登录态。"],
            }
        if auth["state"] == "expired":
            await self.notify_auth_expired()
            return {
                "queried_rooms": 0,
                "subscriptions": 0,
                "alerts": 0,
                "errors": [auth["error"] or "易校园登录态已过期。"],
            }
        subscriptions = self.store.list_due_subscriptions(now=timestamp)
        if not subscriptions:
            return {
                "queried_rooms": 0,
                "subscriptions": 0,
                "alerts": 0,
                "errors": [],
            }
        async with self._scan_lock:
            return await self._query_subscriptions(
                subscriptions,
                now=timestamp,
                send_alerts=True,
            )

    async def query_for_session(
        self,
        umo: str,
        *,
        alias: str = "",
        now: int | None = None,
    ) -> dict[str, Any]:
        subscriptions = self.store.list_subscriptions(umo=umo)
        if alias:
            subscriptions = [
                item
                for item in subscriptions
                if item["alias"].casefold() == alias.casefold()
            ]
            if not subscriptions:
                raise ValueError(f"未找到别名为“{alias}”的寝室订阅。")
        if not subscriptions:
            raise ValueError("当前会话尚未配置寝室订阅。")
        async with self._scan_lock:
            return await self._query_subscriptions(
                subscriptions,
                now=int(now if now is not None else time.time()),
                send_alerts=False,
            )

    async def query_subscription(
        self,
        subscription_id: int,
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        subscription = self.store.get_subscription(subscription_id)
        if not subscription:
            raise ValueError("订阅不存在。")
        async with self._scan_lock:
            return await self._query_subscriptions(
                [subscription],
                now=int(now if now is not None else time.time()),
                send_alerts=False,
            )

    async def _query_subscriptions(
        self,
        subscriptions: list[dict[str, Any]],
        *,
        now: int,
        send_alerts: bool,
    ) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for subscription in subscriptions:
            grouped[subscription["room_key"]].append(subscription)
            self.store.update_runtime(
                subscription["id"],
                last_scan_at=now,
            )

        report: dict[str, Any] = {
            "queried_rooms": 0,
            "subscriptions": len(subscriptions),
            "alerts": 0,
            "items": [],
            "errors": [],
        }
        for room_key, room_subscriptions in grouped.items():
            room = RoomRef.from_dict(room_subscriptions[0]["room"])
            try:
                reading = await self.client.query_surplus(room, captured_at=now)
            except AuthExpiredError as exc:
                message = str(exc)
                for subscription in subscriptions:
                    self.store.update_runtime(
                        subscription["id"],
                        last_error=message,
                    )
                await self.handle_auth_expired(message)
                report["errors"].append(message)
                break
            except Exception as exc:
                message = str(exc)
                for subscription in room_subscriptions:
                    self.store.update_runtime(
                        subscription["id"],
                        last_error=message,
                    )
                self.store.add_diagnostic(
                    room_key,
                    f"寝室查询失败：{message}",
                    level="error",
                    now=now,
                )
                report["errors"].append(
                    f"{room_subscriptions[0]['alias']}：{message}"
                )
                continue

            report["queried_rooms"] += 1
            self.store.save_reading(reading)
            self.store.set_auth_state("valid")
            for subscription in room_subscriptions:
                self.store.update_runtime(
                    subscription["id"],
                    last_success_at=now,
                    last_error="",
                )
                alert_sent = await self._apply_threshold(
                    subscription,
                    reading,
                    send_alerts=send_alerts,
                )
                report["alerts"] += int(alert_sent)
                report["items"].append(
                    {
                        "subscription_id": subscription["id"],
                        "umo": subscription["umo"],
                        "alias": subscription["alias"],
                        "room_key": room_key,
                        "room_name": reading.room_name
                        or room.display_name,
                        "value": display_decimal(reading.value),
                        "balance": (
                            display_decimal(reading.balance)
                            if reading.balance is not None
                            else ""
                        ),
                        "unit": subscription["unit"],
                        "threshold": subscription["threshold"],
                        "captured_at": reading.captured_at,
                        "alert_sent": alert_sent,
                    }
                )
        return report

    async def _apply_threshold(
        self,
        subscription: dict[str, Any],
        reading: Reading,
        *,
        send_alerts: bool,
    ) -> bool:
        threshold = Decimal(subscription["threshold"])
        if reading.value > threshold:
            if subscription["alerted"]:
                self.store.set_alerted(subscription["id"], False)
            return False
        if subscription["alerted"] or not send_alerts:
            return False
        text = (
            "电量提醒\n"
            f"{subscription['alias']} 当前剩余 "
            f"{display_decimal(reading.value)} {subscription['unit']}，"
            f"已达到或低于阈值 {subscription['threshold']} {subscription['unit']}。\n"
            f"寝室：{reading.room_name or RoomRef.from_dict(subscription['room']).display_name}"
        )
        try:
            success = bool(await self.send_callback(subscription["umo"], text))
        except Exception as exc:
            success = False
            error = str(exc)
        else:
            error = "" if success else "消息发送返回失败"
        if success:
            self.store.set_alerted(subscription["id"], True)
            return True
        self.store.add_diagnostic(
            subscription["umo"],
            f"低电量提醒发送失败：{error}",
            level="error",
        )
        return False

    async def handle_auth_expired(self, message: str) -> None:
        current = self.store.auth_snapshot()
        if current["state"] != "expired":
            self.store.add_diagnostic(
                "auth",
                message,
                level="error",
            )
        self.store.set_auth_state("expired", message)
        await self.notify_auth_expired()

    async def notify_auth_expired(self) -> bool:
        auth = self.store.auth_snapshot()
        if auth["state"] != "expired" or auth["notice_sent"]:
            return False
        target = self.store.get_setting("admin_notice_umo", "")
        if not target:
            if self.store.get_setting("auth_notice_missing_logged", "0") != "1":
                self.store.add_diagnostic(
                    "auth",
                    "登录态已过期，但尚未配置管理员私聊通知目标。",
                    level="warning",
                )
                self.store.set_setting("auth_notice_missing_logged", "1")
            return False
        text = (
            "易校园电费监控已暂停：登录态已过期。\n"
            "请在 AstrBot 插件管理页更新 shiroJID"
            "（如有 ymId 可一并填写），验证成功后会自动恢复。"
        )
        try:
            success = bool(await self.send_callback(target, text))
        except Exception as exc:
            success = False
            self.store.add_diagnostic(
                "auth",
                f"管理员登录过期通知发送失败：{exc}",
                level="error",
            )
        if success:
            self.store.mark_auth_notice_sent(True)
        return success

    def mark_credentials_verified(self) -> None:
        self.store.set_auth_state("valid")
        self.store.set_setting("auth_notice_missing_logged", "0")

    async def send_test_notification(self, target: str) -> bool:
        return bool(
            await self.send_callback(
                target,
                "易校园电费监控测试通知：主动消息通道工作正常。",
            )
        )
