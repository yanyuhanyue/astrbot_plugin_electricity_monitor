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

    async def send_message(self, umo, chain):
        self.calls.append((umo, chain))
        return True


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


if __name__ == "__main__":
    unittest.main()

