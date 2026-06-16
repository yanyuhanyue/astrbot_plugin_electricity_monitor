from __future__ import annotations

import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path

from core.client import AuthExpiredError, ElectricityClient
from core.models import RoomRef
from core.service import ElectricityMonitorService
from core.storage import ElectricityStore


class FakeRequest:
    payload = {}

    async def get_json(self, silent=True):
        return self.payload


class FakeContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, *args):
        self.routes.append(args)


class FakeRemote:
    async def query_areas(self):
        return [{"code": "a", "name": "校区"}]

    async def query_buildings(self, _area):
        return [{"code": "b", "name": "楼栋"}]

    async def query_floors(self, _area, _building):
        return [{"code": "f", "name": "楼层"}]

    async def query_rooms(self, _area, _building, _floor):
        return [{"code": "r", "name": "房间"}]


class PageApiTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        quart = types.ModuleType("quart")
        cls.request = FakeRequest()
        quart.request = cls.request
        sys.modules["quart"] = quart
        cls.module = importlib.import_module("core.page_api")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ElectricityStore(Path(self.temp.name))
        self.store.register_session(
            umo="qq:FriendMessage:1",
            platform="qq",
            chat_type="private",
            session_id="1",
            now=1000,
        )
        self.client = ElectricityClient(Path(self.temp.name), session=FakeRemote())
        self.client.set_credentials("very-secret-cookie", "secret-account")
        self.context = FakeContext()

        async def send(_umo, _text):
            return True

        self.service = ElectricityMonitorService(self.store, self.client, send)
        plugin = types.SimpleNamespace(
            store=self.store,
            client=self.client,
            service=self.service,
            context=self.context,
        )

        async def import_sessions():
            return {"added": 0, "discovered": 0, "diagnostics": []}

        plugin.import_existing_sessions = import_sessions
        self.api = self.module.PluginPageApi(plugin)

    def tearDown(self):
        self.temp.cleanup()

    async def test_bootstrap_never_exposes_credentials(self):
        result = await self.api.bootstrap()
        text = str(result)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("very-secret-cookie", text)
        self.assertNotIn("secret-account", text)

    async def test_save_credentials_accepts_missing_ym_id(self):
        async def verify_credentials():
            return [{"code": "a", "name": "校区"}]

        self.client.verify_credentials = verify_credentials
        self.request.payload = {"shiroJID": "new-secret-cookie", "ymId": ""}

        result = await self.api.save_credentials()

        self.assertEqual(result["status"], "ok")
        credentials = result["data"]["credentials"]
        self.assertTrue(credentials["configured"])
        self.assertFalse(credentials["ym_id_configured"])
        self.assertNotIn("new-secret-cookie", str(result))

    async def test_save_credentials_verifies_with_existing_room_before_areas(self):
        saved, _revision = self.store.save_subscription(
            umo="qq:FriendMessage:1",
            room=RoomRef(
                area_id="2510120172541411338",
                area_name="安阳工学院照明、空调",
                building_code="39",
                building_name="厚德苑6号",
                floor_code="71",
                floor_name="2层",
                room_code="12598",
                room_name="6-240空调",
            ),
            config={
                "alias": "宿舍",
                "threshold": "20",
                "unit": "度",
                "interval_seconds": 900,
                "enabled": True,
            },
            expected_revision=self.store.revision,
        )
        area_checks = []
        room_checks = []

        async def verify_credentials():
            area_checks.append(True)
            raise AuthExpiredError("queryArea 返回 204")

        async def query_subscription(subscription_id):
            room_checks.append(subscription_id)
            return {
                "items": [
                    {
                        "subscription_id": subscription_id,
                        "alias": "宿舍",
                        "value": "300.00",
                        "unit": "度",
                        "room_name": "6-240空调",
                        "captured_at": 1000,
                    }
                ],
                "errors": [],
            }

        self.client.verify_credentials = verify_credentials
        self.service.query_subscription = query_subscription
        self.store.set_auth_state("expired", "旧登录态已过期")
        self.request.payload = {"shiroJID": "new-secret-cookie", "ymId": ""}

        result = await self.api.save_credentials()

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["data"]["verified"])
        self.assertEqual(result["data"]["verification"], "room")
        self.assertEqual(room_checks, [saved["id"]])
        self.assertEqual(area_checks, [])
        self.assertEqual(self.store.auth_snapshot()["state"], "valid")

    async def test_route_registration_is_complete(self):
        self.api.register_routes()
        self.assertEqual(len(self.context.routes), 15)
        paths = [item[0] for item in self.context.routes]
        self.assertIn("/astrbot_plugin_electricity_monitor/credentials/save", paths)
        self.assertIn("/astrbot_plugin_electricity_monitor/history", paths)

    async def test_unknown_session_is_rejected(self):
        self.request.payload = {
            "revision": 0,
            "umo": "qq:FriendMessage:404",
            "room": {},
            "config": {},
        }
        guarded = self.api._guard(self.api.save_subscription, "保存订阅")
        result = await guarded()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], 400)

    async def test_save_subscription_accepts_manual_room_codes(self):
        queried = []

        async def query_subscription(subscription_id):
            queried.append(subscription_id)
            return {
                "items": [
                    {
                        "subscription_id": subscription_id,
                        "alias": "宿舍",
                        "value": "300.25",
                        "unit": "度",
                        "room_name": "厚德苑6号240",
                        "captured_at": 1000,
                    }
                ],
                "errors": [],
            }

        self.service.query_subscription = query_subscription
        self.request.payload = {
            "revision": self.store.revision,
            "umo": "qq:FriendMessage:1",
            "room": {
                "area_id": "2510120172541411338",
                "area_name": "",
                "building_code": "39",
                "building_name": "",
                "floor_code": "71",
                "floor_name": "",
                "room_code": "12598",
                "room_name": "厚德苑6号240",
            },
            "config": {
                "alias": "宿舍",
                "threshold": "20",
                "unit": "度",
                "interval_seconds": 900,
                "enabled": True,
            },
        }

        result = await self.api.save_subscription()

        self.assertEqual(result["status"], "ok")
        room = result["data"]["subscription"]["room"]
        self.assertEqual(room["area_id"], "2510120172541411338")
        self.assertEqual(room["room_code"], "12598")
        self.assertEqual(queried, [result["data"]["subscription"]["id"]])
        self.assertEqual(result["data"]["report"]["items"][0]["value"], "300.25")

    async def test_save_subscription_keeps_config_when_initial_query_fails(self):
        async def query_subscription(_subscription_id):
            return {"items": [], "errors": ["易校园登录态已过期"]}

        self.service.query_subscription = query_subscription
        self.request.payload = {
            "revision": self.store.revision,
            "umo": "qq:FriendMessage:1",
            "room": {
                "area_id": "a",
                "area_name": "校区",
                "building_code": "b",
                "building_name": "楼栋",
                "floor_code": "f",
                "floor_name": "楼层",
                "room_code": "r",
                "room_name": "房间",
            },
            "config": {
                "alias": "首次失败",
                "threshold": "20",
                "unit": "度",
                "interval_seconds": 900,
                "enabled": True,
            },
        }

        result = await self.api.save_subscription()

        self.assertEqual(result["status"], "ok")
        self.assertIn("首次查询失败", result["data"]["message"])
        self.assertIsNotNone(
            self.store.find_subscription("qq:FriendMessage:1", "首次失败")
        )

    async def test_admin_notice_rejects_group_target(self):
        self.store.register_session(
            umo="qq:GroupMessage:2",
            platform="qq",
            chat_type="group",
            session_id="2",
            now=1000,
        )
        self.request.payload = {"umo": "qq:GroupMessage:2"}
        guarded = self.api._guard(
            self.api.save_admin_notice,
            "保存通知目标",
        )
        result = await guarded()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["code"], 400)


if __name__ == "__main__":
    unittest.main()
