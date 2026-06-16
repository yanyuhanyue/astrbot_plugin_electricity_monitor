from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from core.session_importer import SessionImporter
from core.session_identity import parse_session_identity
from core.storage import ElectricityStore


class SessionImporterTests(unittest.IsolatedAsyncioTestCase):
    def test_nested_legacy_umo_uses_last_message_type(self):
        parsed = parse_session_identity(
            "default:FriendMessage:default:GroupMessage:779430314"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["chat_type"], "group")
        self.assertEqual(parsed["session_id"], "779430314")

    async def test_imports_private_and_group_targets_and_deduplicates_persona_umo(self):
        context = types.SimpleNamespace(
            conversation_manager=types.SimpleNamespace(
                conversations={
                    "private": {
                        "unified_msg_origin": "qq:FriendMessage:persona!42",
                        "nickname": "测试用户",
                    },
                    "group": {
                        "unified_msg_origin": "qq:GroupMessage:100",
                        "group_name": "测试群",
                    },
                }
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            store = ElectricityStore(Path(temp))
            result = await SessionImporter(context).import_to_store(store)
            sessions = store.list_sessions()
        self.assertEqual(result["discovered"], 2)
        self.assertEqual({item["chat_type"] for item in sessions}, {"private", "group"})
        private = next(item for item in sessions if item["chat_type"] == "private")
        self.assertEqual(private["session_id"], "42")
        self.assertEqual(private["umo"], "qq:FriendMessage:42")

    async def test_nested_group_umo_is_not_imported_as_private(self):
        context = types.SimpleNamespace(
            conversation_manager=types.SimpleNamespace(
                conversations={
                    "legacy": {
                        "unified_msg_origin": (
                            "default:FriendMessage:"
                            "default:GroupMessage:779430314"
                        )
                    }
                }
            )
        )
        result = await SessionImporter(context).collect()
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["chat_type"], "group")
        self.assertEqual(
            result["sessions"][0]["umo"],
            "default:GroupMessage:779430314",
        )

    async def test_reimport_removes_unsubscribed_synthetic_wrong_type(self):
        context = types.SimpleNamespace(
            conversation_manager=types.SimpleNamespace(
                conversations={
                    "legacy": {
                        "unified_msg_origin": (
                            "default:FriendMessage:"
                            "default:GroupMessage:779430314"
                        )
                    }
                }
            )
        )
        with tempfile.TemporaryDirectory() as temp:
            store = ElectricityStore(Path(temp))
            store.register_session(
                umo="default:FriendMessage:779430314",
                platform="default",
                chat_type="private",
                session_id="779430314",
                display_name="私聊 779430314",
                now=900,
            )
            await SessionImporter(context).import_to_store(store)
            sessions = store.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["chat_type"], "group")


if __name__ == "__main__":
    unittest.main()
