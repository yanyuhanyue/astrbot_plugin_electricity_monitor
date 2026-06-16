from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from http.cookies import SimpleCookie
from pathlib import Path

import aiohttp

from core.client import (
    AuthExpiredError,
    ElectricityApiError,
    ElectricityClient,
)
from core.models import RoomRef


class FakeResponse:
    def __init__(self, payload, status=200, raw=False, set_cookie=""):
        self.payload = payload
        self.status = status
        self.raw = raw
        self.cookies = SimpleCookie()
        if set_cookie:
            self.cookies.load(set_cookie)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def text(self):
        return self.payload if self.raw else json.dumps(self.payload, ensure_ascii=False)


class FakeSession:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.responses.pop(0)

    async def close(self):
        pass


def selected_room():
    return RoomRef(
        area_id="a",
        area_name="校区",
        building_code="b",
        building_name="楼栋",
        floor_code="f",
        floor_name="楼层",
        room_code="r",
        room_name="房间",
    )


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def client(self, responses=None, error=None):
        client = ElectricityClient(
            Path(self.temp.name),
            session=FakeSession(responses, error),
        )
        client.request_interval = 0
        client.set_credentials("secret-cookie", "account-id")
        return client

    async def test_full_location_chain_and_surplus(self):
        client = self.client(
            [
                FakeResponse({"success": True, "rows": [{"areaId": "a", "areaName": "校区"}]}),
                FakeResponse({"success": True, "rows": [{"buildingCode": "b", "buildingName": "楼栋"}]}),
                FakeResponse({"success": True, "rows": [{"floorCode": "f", "floorName": "楼层"}]}),
                FakeResponse({"success": True, "rows": [{"roomCode": "r", "roomName": "房间"}]}),
                FakeResponse({"success": True, "data": {"amount": "18.50", "displayRoomName": "240"}}),
            ]
        )
        self.assertEqual((await client.query_areas())[0]["code"], "a")
        self.assertEqual((await client.query_buildings("a"))[0]["code"], "b")
        self.assertEqual((await client.query_floors("a", "b"))[0]["code"], "f")
        self.assertEqual((await client.query_rooms("a", "b", "f"))[0]["code"], "r")
        reading = await client.query_surplus(selected_room(), captured_at=1000)
        self.assertEqual(str(reading.value), "18.50")
        self.assertEqual(reading.room_name, "240")
        call = client._session.calls[-1]
        self.assertNotIn("secret-cookie", call[0])
        self.assertEqual(call[1]["params"]["platform"], "YUNMA_APP")

    async def test_query_surplus_prefers_surplus_over_amount(self):
        client = self.client(
            [
                FakeResponse(
                    {
                        "statusCode": 0,
                        "message": "操作成功",
                        "success": True,
                        "data": {
                            "surplus": 300.79,
                            "amount": 160.32,
                            "isShowSurplus": 0,
                            "isShowMoney": 1,
                            "displayRoomName": "安阳工学院照明、空调厚德苑6号楼2层6-240空调",
                        },
                    }
                )
            ]
        )

        reading = await client.query_surplus(selected_room(), captured_at=1000)

        self.assertEqual(str(reading.value), "300.79")
        self.assertEqual(str(reading.balance), "160.32")
        self.assertIn("6-240空调", reading.room_name)

    async def test_area_uses_id_for_building_query(self):
        client = self.client(
            [
                FakeResponse(
                    {
                        "success": True,
                        "rows": [
                            {
                                "id": "2510120172541411338",
                                "areaId": "display-only-area-id",
                                "areaName": "安阳工学院照明、空调",
                            }
                        ],
                    }
                ),
                FakeResponse(
                    {
                        "success": True,
                        "rows": [
                            {
                                "buildingCode": "39",
                                "buildingName": "厚德苑6号",
                            }
                        ],
                    }
                ),
            ]
        )

        area = (await client.query_areas())[0]
        buildings = await client.query_buildings(area["code"])

        self.assertEqual(area["code"], "2510120172541411338")
        self.assertEqual(buildings[0]["code"], "39")
        self.assertEqual(
            client._session.calls[1][1]["params"]["areaId"],
            "2510120172541411338",
        )

    async def test_surplus_request_allows_missing_ym_id(self):
        client = ElectricityClient(
            Path(self.temp.name),
            session=FakeSession(
                [
                    FakeResponse(
                        {
                            "success": True,
                            "data": {
                                "amount": "21.50",
                                "displayRoomName": "厚德苑6号240",
                            },
                        }
                    )
                ]
            ),
        )
        client.request_interval = 0
        client.set_credentials("secret-cookie", "")

        reading = await client.query_surplus(selected_room(), captured_at=1000)

        self.assertEqual(str(reading.value), "21.50")
        params = client._session.calls[0][1]["params"]
        self.assertNotIn("ymId", params)
        self.assertTrue(client.credential_status()["configured"])
        self.assertFalse(client.credential_status()["ym_id_configured"])

    async def test_auth_expired_response(self):
        client = self.client(
            [FakeResponse({"success": False, "statusCode": 204, "message": "请重新登录"})]
        )
        with self.assertRaises(AuthExpiredError):
            await client.query_areas()

    async def test_success_response_rotates_shiro_cookie(self):
        client = self.client(
            [
                FakeResponse(
                    {"success": True, "rows": [{"areaId": "a", "areaName": "校区"}]},
                    set_cookie="shiroJID=rotated-cookie; Path=/; Secure; HttpOnly",
                ),
                FakeResponse(
                    {"success": True, "rows": [{"areaId": "a", "areaName": "校区"}]}
                ),
            ]
        )

        await client.query_areas()
        await client.query_areas()

        second_headers = client._session.calls[1][1]["headers"]
        self.assertEqual(second_headers["Cookie"], "shiroJID=rotated-cookie")
        saved = json.loads(client.credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["shiroJID"], "rotated-cookie")

    async def test_expired_response_does_not_replace_cookie(self):
        client = self.client(
            [
                FakeResponse(
                    {
                        "success": False,
                        "statusCode": 204,
                        "message": "请重新登录",
                    },
                    set_cookie="shiroJID=anonymous-cookie; Path=/; Secure; HttpOnly",
                )
            ]
        )

        with self.assertRaises(AuthExpiredError):
            await client.query_areas()

        saved = json.loads(client.credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["shiroJID"], "secret-cookie")

    async def test_invalid_json_and_server_error(self):
        client = self.client([FakeResponse("not-json", raw=True)])
        with self.assertRaises(ElectricityApiError):
            await client.query_areas()
        client = self.client([FakeResponse({}, status=503)])
        with self.assertRaises(ElectricityApiError):
            await client.query_areas()

    async def test_timeout_is_normalized(self):
        client = self.client(error=asyncio.TimeoutError())
        with self.assertRaisesRegex(ElectricityApiError, "超时"):
            await client.query_areas()

    async def test_network_error_redacts_credentials(self):
        client = self.client(
            error=aiohttp.ClientConnectionError(
                "request account-id failed with secret-cookie"
            )
        )
        with self.assertRaises(ElectricityApiError) as captured:
            await client.query_areas()
        message = str(captured.exception)
        self.assertNotIn("account-id", message)
        self.assertNotIn("secret-cookie", message)
        self.assertIn("***", message)

    def test_credentials_are_masked_and_not_returned(self):
        client = self.client([])
        status = client.credential_status()
        self.assertTrue(status["configured"])
        self.assertNotIn("secret-cookie", str(status))
        self.assertNotIn("account-id", str(status))
        self.assertIn("secret-cookie", client.credentials_path.read_text(encoding="utf-8"))

    def test_full_cookie_text_extracts_only_shiro_jid(self):
        client = ElectricityClient(
            Path(self.temp.name),
            session=FakeSession(),
        )

        client.set_credentials(
            "Cookie: other=discarded; shiroJID=session-value; theme=discarded",
            "",
        )

        saved = json.loads(client.credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["shiroJID"], "session-value")
        self.assertNotIn("other", str(saved))
        self.assertNotIn("theme", str(saved))


if __name__ == "__main__":
    unittest.main()
