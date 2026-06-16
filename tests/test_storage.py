from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from core.models import Reading, RoomRef
from core.storage import ElectricityStore, RevisionConflict


def room(code: str = "240") -> RoomRef:
    return RoomRef(
        area_id="area",
        area_name="安阳工学院",
        building_code="building",
        building_name="厚德苑6号",
        floor_code="2",
        floor_name="2层",
        room_code=code,
        room_name=code,
    )


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ElectricityStore(Path(self.temp.name))
        self.umo = "qq:GroupMessage:1"
        self.store.register_session(
            umo=self.umo,
            platform="qq",
            chat_type="group",
            session_id="1",
            display_name="测试群",
            now=1000,
        )

    def tearDown(self):
        self.temp.cleanup()

    def save(self, alias="240", selected_room=None, revision=None):
        return self.store.save_subscription(
            umo=self.umo,
            room=selected_room or room(),
            config={
                "alias": alias,
                "unit": "度",
                "threshold": "20",
                "interval_seconds": 900,
                "enabled": True,
            },
            expected_revision=revision,
            now=1000,
        )

    def test_multiple_rooms_per_session_and_alias_uniqueness(self):
        first, _ = self.save("240", room("240"))
        second, _ = self.save("241", room("241"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list_subscriptions(umo=self.umo)), 2)
        with self.assertRaises(ValueError):
            self.save("240", room("242"))

    def test_revision_conflict(self):
        _saved, revision = self.save(revision=0)
        self.assertEqual(revision, 1)
        with self.assertRaises(RevisionConflict):
            self.save("other", room("241"), revision=0)

    def test_readings_restore_after_restart_and_expire_after_30_days(self):
        saved, _ = self.save()
        self.store.save_reading(
            Reading(
                room_key=saved["room_key"],
                value=Decimal("19.75"),
                balance=Decimal("8.50"),
                room_name="厚德苑6号240",
                captured_at=1000,
            )
        )
        reopened = ElectricityStore(Path(self.temp.name))
        self.assertEqual(
            reopened.list_subscriptions(umo=self.umo)[0]["latest_value"],
            "19.75",
        )
        self.assertEqual(
            reopened.list_subscriptions(umo=self.umo)[0]["latest_balance"],
            "8.50",
        )
        self.assertEqual(
            reopened.get_history(saved["room_key"])[0]["balance"],
            "8.50",
        )
        reopened.remove_old_data(now=1000 + 31 * 86_400)
        self.assertEqual(reopened.get_history(saved["room_key"]), [])

    def test_due_runtime_and_alert_state(self):
        saved, _ = self.save()
        self.assertEqual(
            len(self.store.list_due_subscriptions(now=1000)),
            1,
        )
        self.store.update_runtime(saved["id"], last_scan_at=1000)
        self.assertEqual(self.store.list_due_subscriptions(now=1500), [])
        self.assertEqual(
            len(self.store.list_due_subscriptions(now=1900)),
            1,
        )
        self.store.set_alerted(saved["id"], True)
        self.assertTrue(self.store.get_subscription(saved["id"])["alerted"])

    def test_changing_room_resets_alert_state(self):
        saved, _ = self.save()
        self.store.set_alerted(saved["id"], True)
        updated, _ = self.store.save_subscription(
            umo=self.umo,
            room=room("241"),
            config={
                "alias": "240",
                "unit": "度",
                "threshold": "20",
                "interval_seconds": 900,
                "enabled": True,
            },
            subscription_id=saved["id"],
            now=1001,
        )
        self.assertFalse(updated["alerted"])
        self.assertEqual(updated["room"]["room_code"], "241")

    def test_admin_notice_target_must_be_private(self):
        with self.assertRaises(ValueError):
            self.store.set_admin_notice_umo(self.umo)
        private = "qq:FriendMessage:9"
        self.store.register_session(
            umo=private,
            platform="qq",
            chat_type="private",
            session_id="9",
            now=1000,
        )
        self.store.set_admin_notice_umo(private)
        self.assertEqual(
            self.store.get_setting("admin_notice_umo"),
            private,
        )


if __name__ == "__main__":
    unittest.main()
