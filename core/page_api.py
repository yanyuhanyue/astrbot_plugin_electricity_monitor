"""Dashboard API for the electricity monitor plugin."""

from __future__ import annotations

import inspect
import logging
import time
from functools import wraps
from typing import Any

from quart import request

from .client import AuthExpiredError
from .models import RoomRef
from .session_identity import parse_session_identity
from .storage import RevisionConflict, dedupe_sessions


PLUGIN_NAME = "astrbot_plugin_electricity_monitor"
PAGE_API_PREFIX = f"/{PLUGIN_NAME}"
log = logging.getLogger(__name__)


class PluginPageApi:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def register_routes(self) -> None:
        routes = (
            ("bootstrap", self.bootstrap, ["GET"], "读取电费监控数据"),
            ("sessions/import", self.import_sessions, ["POST"], "导入 AstrBot 会话"),
            ("credentials/save", self.save_credentials, ["POST"], "保存并验证登录态"),
            ("credentials/verify", self.verify_credentials, ["POST"], "验证登录态"),
            ("credentials/clear", self.clear_credentials, ["POST"], "清除登录态"),
            ("locations/areas", self.areas, ["POST"], "读取校区"),
            ("locations/buildings", self.buildings, ["POST"], "读取楼栋"),
            ("locations/floors", self.floors, ["POST"], "读取楼层"),
            ("locations/rooms", self.rooms, ["POST"], "读取寝室"),
            ("subscriptions/save", self.save_subscription, ["POST"], "保存寝室订阅"),
            ("subscriptions/delete", self.delete_subscription, ["POST"], "删除寝室订阅"),
            ("query/run", self.run_query, ["POST"], "立即查询寝室"),
            ("history", self.history, ["POST"], "读取电量历史"),
            ("settings/admin-notice", self.save_admin_notice, ["POST"], "保存管理员通知目标"),
            ("notification/test", self.test_notification, ["POST"], "发送测试通知"),
        )
        for endpoint, handler, methods, description in routes:
            self.plugin.context.register_web_api(
                f"{PAGE_API_PREFIX}/{endpoint}",
                self._guard(handler, description),
                methods,
                description,
            )

    async def bootstrap(self):
        store = self._store()
        status = self._client().credential_status()
        status.update(store.auth_snapshot())
        return self._ok(
            {
                "revision": store.revision,
                "sessions": dedupe_sessions(store.list_sessions()),
                "subscriptions": store.list_subscriptions(),
                "diagnostics": store.list_diagnostics(),
                "credentials": status,
                "admin_notice_umo": store.get_setting("admin_notice_umo", ""),
            }
        )

    async def import_sessions(self):
        await self._require_dashboard_admin()
        result = await self.plugin.import_existing_sessions()
        repaired = int(result.get("repaired", 0))
        return self._ok(
            {
                "message": (
                    f"已导入历史会话：新增 {result.get('added', 0)} 个，"
                    f"发现 {result.get('discovered', 0)} 个"
                    + (f"，修复 {repaired} 个错误会话。" if repaired else "。")
                ),
                **result,
            }
        )

    async def save_credentials(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        shiro_jid = self._required(payload, "shiroJID")
        ym_id = str(payload.get("ymId", "") or "").strip()
        self._client().set_credentials(shiro_jid, ym_id)
        self._store().set_auth_state("unknown", "")
        try:
            verification = await self._verify_login_state()
        except AuthExpiredError as exc:
            await self._service().handle_auth_expired(str(exc))
            raise
        except Exception as exc:
            self._store().set_auth_state("unknown", str(exc))
            self._store().add_diagnostic(
                "auth",
                f"登录态验证失败：{exc}",
                level="warning" if not ym_id else "error",
            )
            if ym_id:
                raise
            return self._ok(
                {
                    "message": (
                        "shiroJID 已保存；校区接口未能完成验证，"
                        "请使用手动房间参数保存订阅并立即查询。"
                    ),
                    "verified": False,
                    "area_count": 0,
                    "verification": "none",
                    "credentials": {
                        **self._client().credential_status(),
                        **self._store().auth_snapshot(),
                    },
                }
            )
        self._service().mark_credentials_verified()
        return self._ok(
            {
                "message": (
                    "登录态已保存，并通过已有寝室查询验证成功。"
                    if verification["verification"] == "room"
                    else "登录态已保存并验证成功。"
                ),
                "verified": True,
                **verification,
                "credentials": {
                    **self._client().credential_status(),
                    **self._store().auth_snapshot(),
                },
            }
        )

    async def verify_credentials(self):
        await self._require_dashboard_admin()
        self._store().set_auth_state("unknown", "")
        try:
            verification = await self._verify_login_state()
        except AuthExpiredError as exc:
            await self._service().handle_auth_expired(str(exc))
            raise
        except Exception as exc:
            self._store().set_auth_state("unknown", str(exc))
            self._store().add_diagnostic(
                "auth",
                f"登录态验证失败：{exc}",
                level="error",
            )
            raise
        self._service().mark_credentials_verified()
        return self._ok(
            {
                "message": (
                    "登录态有效，已通过已有寝室查询验证。"
                    if verification["verification"] == "room"
                    else "登录态有效。"
                ),
                **verification,
            }
        )

    async def clear_credentials(self):
        await self._require_dashboard_admin()
        self._client().clear_credentials()
        self._store().set_auth_state("unconfigured", "")
        self._store().mark_auth_notice_sent(False)
        return self._ok({"message": "登录态已清除。"})

    async def areas(self):
        await self._require_dashboard_admin()
        return self._ok({"items": await self._client().query_areas()})

    async def buildings(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        return self._ok(
            {
                "items": await self._client().query_buildings(
                    self._required(payload, "area_id")
                )
            }
        )

    async def floors(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        return self._ok(
            {
                "items": await self._client().query_floors(
                    self._required(payload, "area_id"),
                    self._required(payload, "building_code"),
                )
            }
        )

    async def rooms(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        return self._ok(
            {
                "items": await self._client().query_rooms(
                    self._required(payload, "area_id"),
                    self._required(payload, "building_code"),
                    self._required(payload, "floor_code"),
                )
            }
        )

    async def save_subscription(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        subscription_id = payload.get("subscription_id")
        saved, revision = self._store().save_subscription(
            umo=self._known_umo(payload),
            room=RoomRef.from_dict(payload.get("room")),
            config=payload.get("config"),
            subscription_id=(
                int(subscription_id) if subscription_id not in {None, ""} else None
            ),
            expected_revision=self._revision(payload),
        )
        report = await self._service().query_subscription(saved["id"])
        errors = report.get("errors") or []
        message = (
            f"寝室订阅已保存，但首次查询失败：{'；'.join(errors)}"
            if errors
            else "寝室订阅已保存并完成首次查询。"
        )
        refreshed = self._store().get_subscription(saved["id"]) or saved
        return self._ok(
            {
                "message": message,
                "revision": revision,
                "subscription": refreshed,
                "report": report,
            }
        )

    async def delete_subscription(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        revision = self._store().delete_subscription(
            int(self._required(payload, "subscription_id")),
            umo=self._known_umo(payload),
            expected_revision=self._revision(payload),
        )
        return self._ok({"message": "寝室订阅已删除。", "revision": revision})

    async def run_query(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        subscription_id = int(self._required(payload, "subscription_id"))
        subscription = self._store().get_subscription(subscription_id)
        if not subscription:
            raise ValueError("订阅不存在。")
        self._known_umo({"umo": subscription["umo"]})
        report = await self._service().query_subscription(subscription_id)
        return self._ok({"message": "查询完成。", "report": report})

    async def history(self):
        payload = await self._json_payload()
        subscription = self._store().get_subscription(
            int(self._required(payload, "subscription_id"))
        )
        if not subscription:
            raise ValueError("订阅不存在。")
        since = int(time.time()) - 30 * 86_400
        return self._ok(
            {
                "subscription": subscription,
                "items": self._store().get_history(
                    subscription["room_key"],
                    since=since,
                ),
            }
        )

    async def save_admin_notice(self):
        await self._require_dashboard_admin()
        payload = await self._json_payload()
        umo = str(payload.get("umo", "") or "").strip()
        if umo:
            self._known_umo({"umo": umo})
            self._store().set_admin_notice_umo(umo)
        else:
            self._store().set_setting("admin_notice_umo", "")
        self._store().mark_auth_notice_sent(False)
        self._store().set_setting("auth_notice_missing_logged", "0")
        return self._ok({"message": "管理员通知目标已保存。"})

    async def test_notification(self):
        await self._require_dashboard_admin()
        target = self._store().get_setting("admin_notice_umo", "")
        if not target:
            raise ValueError("请先选择管理员私聊通知目标。")
        success = await self._service().send_test_notification(target)
        if not success:
            raise RuntimeError("主动消息发送返回失败。")
        return self._ok({"message": "测试通知已发送。"})

    async def _verify_login_state(self) -> dict[str, Any]:
        subscriptions = self._store().list_subscriptions()
        errors = []
        for subscription in subscriptions[:3]:
            report = await self._service().query_subscription(subscription["id"])
            if report.get("items"):
                return {
                    "verification": "room",
                    "area_count": 0,
                    "subscription_id": subscription["id"],
                }
            errors.extend(str(item) for item in report.get("errors") or [])
            if self._store().auth_snapshot()["state"] == "expired":
                raise AuthExpiredError(
                    errors[-1] if errors else "易校园登录态已过期。"
                )
        if subscriptions and errors:
            raise RuntimeError("；".join(errors))
        areas = await self._client().verify_credentials()
        return {
            "verification": "area",
            "area_count": len(areas),
        }

    def _store(self):
        if self.plugin.store is None:
            raise RuntimeError("插件尚未完成初始化。")
        return self.plugin.store

    def _client(self):
        if self.plugin.client is None:
            raise RuntimeError("易校园客户端尚未完成初始化。")
        return self.plugin.client

    def _service(self):
        if self.plugin.service is None:
            raise RuntimeError("电费监控服务尚未完成初始化。")
        return self.plugin.service

    def _guard(self, handler, description: str):
        @wraps(handler)
        async def guarded():
            try:
                return await handler()
            except RevisionConflict as exc:
                return self._error(str(exc), 409)
            except PermissionError as exc:
                return self._error(str(exc), 403)
            except AuthExpiredError as exc:
                return self._error(str(exc), 401)
            except (ValueError, TypeError) as exc:
                return self._error(str(exc), 400)
            except Exception as exc:
                log.exception("%s失败", description)
                return self._error(f"{description}失败：{exc}", 500)

        return guarded

    @staticmethod
    async def _json_payload() -> dict[str, Any]:
        payload = await request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是对象。")
        return payload

    @staticmethod
    def _required(payload: dict[str, Any], name: str) -> str:
        value = str(payload.get(name, "") or "").strip()
        if not value:
            raise ValueError(f"缺少参数：{name}")
        return value

    def _known_umo(self, payload: dict[str, Any]) -> str:
        umo = self._required(payload, "umo")
        if not parse_session_identity(umo):
            raise ValueError("无效会话标识。")
        if not self._store().get_session(umo):
            raise ValueError("会话不存在，请先让机器人收到该会话消息。")
        return umo

    async def _require_dashboard_admin(self) -> None:
        checker = getattr(self.plugin, "is_dashboard_admin_request", None)
        if not callable(checker):
            return
        allowed = checker(request)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if not allowed:
            raise PermissionError("WebUI 权限不足，只有管理员可以执行此操作。")

    @staticmethod
    def _revision(payload: dict[str, Any]) -> int:
        try:
            return int(payload["revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("缺少有效的数据版本号，请刷新页面。") from exc

    @staticmethod
    def _ok(data: dict[str, Any]):
        return {"status": "ok", "data": data}

    @staticmethod
    def _error(message: str, status: int):
        return {"status": "error", "message": message, "code": status}
