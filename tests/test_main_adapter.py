from __future__ import annotations

import importlib
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path


def install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    star = types.ModuleType("astrbot.api.star")
    components = types.ModuleType("astrbot.api.message_components")

    class Filter:
        class EventMessageType:
            ALL = "all"

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda function: function

        @staticmethod
        def event_message_type(*_args, **_kwargs):
            return lambda function: function

    class MessageChain(list):
        pass

    class Plain:
        def __init__(self, text):
            self.text = text

    class Star:
        def __init__(self, context):
            self.context = context

    class StarTools:
        @staticmethod
        def get_data_dir(_name):
            return Path(".")

    def register(*_args, **_kwargs):
        return lambda target: target

    api.AstrBotConfig = dict
    api.logger = logging.getLogger("electricity-test")
    event.AstrMessageEvent = object
    event.MessageChain = MessageChain
    event.filter = Filter()
    star.Context = object
    star.Star = Star
    star.StarTools = StarTools
    star.register = register
    components.Plain = Plain
    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event,
            "astrbot.api.star": star,
            "astrbot.api.message_components": components,
        }
    )
    return Plain


class FakeContext:
    def __init__(self):
        self.calls = []
        self.platform_manager = types.SimpleNamespace(platform_insts=[])

    async def send_message(self, umo, chain):
        self.calls.append((umo, chain))
        return True


class FakeBot:
    def __init__(self, result):
        self.result = result
        self.private_calls = []
        self.group_calls = []

    async def send_private_msg(self, **kwargs):
        self.private_calls.append(kwargs)
        return self.result

    async def send_group_msg(self, **kwargs):
        self.group_calls.append(kwargs)
        return self.result


class FakePlatform:
    def __init__(self, bot, platform_id="qq"):
        self.bot = bot
        self._platform_id = platform_id

    def meta(self):
        return types.SimpleNamespace(id=self._platform_id, name=self._platform_id)


class FakeEvent:
    def __init__(self, text="/电费", private=True, role="member"):
        self.text = text
        self.private = private
        self.unified_msg_origin = (
            "qq:FriendMessage:1" if private else "qq:GroupMessage:2"
        )
        self.message_obj = types.SimpleNamespace(
            raw_message={"sender": {"role": role}},
            group=None,
        )

    def is_private_chat(self):
        return self.private

    def is_admin(self):
        return False

    def get_sender_id(self):
        return "1"

    def get_group_id(self):
        return "2"

    def get_sender_name(self):
        return "测试用户"

    def get_group_name(self):
        return "测试群"

    def get_platform_id(self):
        return "qq"

    def get_message_str(self):
        return self.text

    def plain_result(self, text):
        return text


class MainAdapterTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.Plain = install_astrbot_stubs()
        parent = str(Path(__file__).resolve().parents[2])
        if parent not in sys.path:
            sys.path.insert(0, parent)
        cls.module = importlib.import_module(
            "astrbot_plugin_electricity_monitor.main"
        )
        cls.core = importlib.import_module(
            "astrbot_plugin_electricity_monitor.core"
        )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = FakeContext()
        self.plugin = self.module.ElectricityMonitorPlugin(self.context)
        self.plugin.store = self.core.ElectricityStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    async def test_private_user_can_manage_own_subscription(self):
        self.assertTrue(await self.plugin._can_manage(FakeEvent(private=True)))

    async def test_group_member_cannot_manage_but_group_admin_can(self):
        self.assertFalse(
            await self.plugin._can_manage(
                FakeEvent(private=False, role="member")
            )
        )
        self.assertTrue(
            await self.plugin._can_manage(
                FakeEvent(private=False, role="admin")
            )
        )

    async def test_send_text_uses_plain_component(self):
        self.assertTrue(await self.plugin._send_text("qq:FriendMessage:1", "测试"))
        self.assertIsInstance(self.context.calls[0][1][0], self.Plain)

    async def test_send_text_prefers_onebot_direct_response(self):
        bot = FakeBot({"status": "ok", "retcode": 0, "data": {"message_id": 123}})
        self.context.platform_manager.platform_insts = [FakePlatform(bot)]

        self.assertTrue(await self.plugin._send_text("qq:FriendMessage:21", "测试"))

        self.assertEqual(self.context.calls, [])
        self.assertEqual(bot.private_calls[0]["user_id"], 21)

    async def test_send_text_rejects_onebot_failure_response(self):
        bot = FakeBot({"status": "failed", "retcode": 1200, "wording": "发送失败"})
        self.context.platform_manager.platform_insts = [FakePlatform(bot)]

        self.assertFalse(await self.plugin._send_text("qq:FriendMessage:21", "测试"))

        self.assertEqual(self.context.calls, [])
        self.assertEqual(bot.private_calls[0]["user_id"], 21)


if __name__ == "__main__":
    unittest.main()
