from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from core.client import AuthExpiredError
from core.models import Reading, RoomRef
from core.service import ElectricityMonitorService
from core.storage import ElectricityStore


def room():
    return RoomRef(
        area_id="a",
        area_name="校区",
        building_code="b",
        building_name="厚德苑6号",
        floor_code="2",
        floor_name="2层",
        room_code="240",
        room_name="240",
    )


class FakeClient:
    def __init__(self, values=None, error=None, balances=None):
        self.values = list(values or ["30"])
        self.balances = list(balances or [])
        self.error = error
        self.calls = 0

    async def query_surplus(self, selected_room, *, captured_at=None):
        self.calls += 1
        if self.error:
            raise self.error
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        balance = self.balances.pop(0) if self.balances else None
        return Reading(
            room_key=selected_room.key,
            value=Decimal(value),
            balance=Decimal(balance) if balance is not None else None,
            room_name="厚德苑6号240",
            captured_at=int(captured_at or 0),
        )


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ElectricityStore(Path(self.temp.name))
        self.sent = []
        self.outcomes = []
        self.private = "qq:FriendMessage:9"
        for umo, chat_type, session_id in (
            ("qq:GroupMessage:1", "group", "1"),
            ("qq:GroupMessage:2", "group", "2"),
            (self.private, "private", "9"),
        ):
            self.store.register_session(
                umo=umo,
                platform="qq",
                chat_type=chat_type,
                session_id=session_id,
                now=1000,
            )
        self.store.set_auth_state("valid")

    def tearDown(self):
        self.temp.cleanup()

    def subscribe(self, umo, alias, threshold="20"):
        saved, _ = self.store.save_subscription(
            umo=umo,
            room=room(),
            config={
                "alias": alias,
                "unit": "度",
                "threshold": threshold,
                "interval_seconds": 300,
                "enabled": True,
            },
            now=900,
        )
        return saved

    async def send(self, umo, text):
        self.sent.append((umo, text))
        return self.outcomes.pop(0) if self.outcomes else True

    async def test_same_room_is_queried_once_for_multiple_subscriptions(self):
        self.subscribe("qq:GroupMessage:1", "一群")
        self.subscribe("qq:GroupMessage:2", "二群")
        client = FakeClient(["30"])
        service = ElectricityMonitorService(self.store, client, self.send)
        report = await service.scan_due(now=1000)
        self.assertEqual(client.calls, 1)
        self.assertEqual(report["subscriptions"], 2)
        self.assertEqual(len(report["items"]), 2)

    async def test_query_report_includes_balance(self):
        self.subscribe("qq:GroupMessage:1", "240")
        service = ElectricityMonitorService(
            self.store,
            FakeClient(["300.79"], balances=["160.32"]),
            self.send,
        )

        report = await service.query_for_session("qq:GroupMessage:1", now=1000)

        self.assertEqual(report["items"][0]["value"], "300.79")
        self.assertEqual(report["items"][0]["balance"], "160.32")
        self.assertEqual(
            self.store.list_subscriptions(umo="qq:GroupMessage:1")[0][
                "latest_balance"
            ],
            "160.32",
        )

    async def test_unconfigured_credentials_do_not_trigger_expiry_notice(self):
        self.subscribe("qq:GroupMessage:1", "240")
        self.store.set_admin_notice_umo(self.private)
        self.store.set_auth_state("unconfigured")
        service = ElectricityMonitorService(self.store, FakeClient(["30"]), self.send)
        report = await service.scan_due(now=1000)
        self.assertEqual(report["queried_rooms"], 0)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.auth_snapshot()["state"], "unconfigured")

    async def test_crossing_alerts_once_and_rearms_after_recovery(self):
        subscription = self.subscribe("qq:GroupMessage:1", "240")
        client = FakeClient(["19", "18", "25", "17"])
        service = ElectricityMonitorService(self.store, client, self.send)
        first = await service.scan_due(now=1000)
        second = await service.scan_due(now=1300)
        third = await service.scan_due(now=1600)
        fourth = await service.scan_due(now=1900)
        self.assertEqual([first["alerts"], second["alerts"], third["alerts"], fourth["alerts"]], [1, 0, 0, 1])
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.store.get_subscription(subscription["id"])["alerted"])

    async def test_failed_alert_retries_next_poll(self):
        subscription = self.subscribe("qq:GroupMessage:1", "240")
        self.outcomes = [False, True]
        service = ElectricityMonitorService(self.store, FakeClient(["19"]), self.send)
        first = await service.scan_due(now=1000)
        second = await service.scan_due(now=1300)
        self.assertEqual(first["alerts"], 0)
        self.assertEqual(second["alerts"], 1)
        self.assertTrue(self.store.get_subscription(subscription["id"])["alerted"])

    async def test_manual_query_does_not_consume_background_alert(self):
        subscription = self.subscribe("qq:GroupMessage:1", "240")
        service = ElectricityMonitorService(self.store, FakeClient(["19"]), self.send)
        report = await service.query_for_session("qq:GroupMessage:1", now=1000)
        self.assertEqual(report["alerts"], 0)
        self.assertFalse(self.store.get_subscription(subscription["id"])["alerted"])

    async def test_auth_expiry_pauses_and_notifies_admin_once(self):
        self.subscribe("qq:GroupMessage:1", "240")
        self.store.set_admin_notice_umo(self.private)
        service = ElectricityMonitorService(
            self.store,
            FakeClient(error=AuthExpiredError("请重新登录")),
            self.send,
        )
        first = await service.scan_due(now=1000)
        second = await service.scan_due(now=1300)
        self.assertEqual(first["alerts"], 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("登录态已过期", self.sent[0][1])
        self.assertEqual(second["queried_rooms"], 0)
        self.assertEqual(len(self.sent), 1)

    async def test_failed_auth_notice_retries_until_success(self):
        self.store.set_admin_notice_umo(self.private)
        self.store.set_auth_state("expired", "请重新登录")
        self.outcomes = [False, True]
        service = ElectricityMonitorService(self.store, FakeClient(), self.send)
        self.assertFalse(await service.notify_auth_expired())
        self.assertTrue(await service.notify_auth_expired())
        self.assertTrue(self.store.auth_snapshot()["notice_sent"])


if __name__ == "__main__":
    unittest.main()
