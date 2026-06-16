"""Async client for the Yi Campus electricity endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import aiohttp

from .models import Reading, RoomRef, decimal_value


class ElectricityClientError(RuntimeError):
    """Base API client error."""


class AuthExpiredError(ElectricityClientError):
    """The Yi Campus login state is missing or expired."""


class ElectricityApiError(ElectricityClientError):
    """The remote API returned an invalid or failed response."""


class ElectricityClient:
    BASE_URL = "https://application.xiaofubao.com/app/electric"

    def __init__(
        self,
        data_dir: Path,
        *,
        request_interval: float = 2.0,
        timeout_seconds: float = 20.0,
        session: Any | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.credentials_path = self.data_dir / "credentials.json"
        self.request_interval = max(2.0, float(request_interval))
        self.timeout_seconds = max(3.0, float(timeout_seconds))
        self._session = session
        self._owns_session = session is None
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._credentials = self._load_credentials()

    async def initialize(self) -> None:
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
        self._session = None

    def credential_status(self) -> dict[str, Any]:
        shiro_jid = self._credentials.get("shiroJID", "")
        ym_id = self._credentials.get("ymId", "")
        return {
            "configured": bool(shiro_jid),
            "ym_id_configured": bool(ym_id),
            "shiro_jid_masked": _mask(shiro_jid),
            "ym_id_masked": _mask(ym_id),
        }

    def set_credentials(self, shiro_jid: str, ym_id: str) -> None:
        shiro = _normalize_shiro_jid(shiro_jid)
        account = str(ym_id or "").strip()
        if not shiro:
            raise ValueError("shiroJID 不能为空。")
        self._credentials = {"shiroJID": shiro, "ymId": account}
        self._atomic_write_credentials()

    def clear_credentials(self) -> None:
        self._credentials = {}
        try:
            self.credentials_path.unlink(missing_ok=True)
        except OSError:
            pass

    async def verify_credentials(self) -> list[dict[str, str]]:
        return await self.query_areas()

    async def query_areas(self) -> list[dict[str, str]]:
        payload = await self._request("queryArea", {"type": 1})
        return _location_rows(
            payload,
            # queryBuilding expects queryArea.rows[].id. Some deployments also
            # expose an areaId field with a different, non-query identifier.
            code_fields=("id", "areaId", "areaCode", "code"),
            name_fields=("areaName", "name", "displayName"),
        )

    async def query_buildings(self, area_id: str) -> list[dict[str, str]]:
        payload = await self._request("queryBuilding", {"areaId": area_id})
        return _location_rows(
            payload,
            code_fields=("buildingCode", "buildingId", "id", "code"),
            name_fields=("buildingName", "name", "displayName"),
        )

    async def query_floors(
        self,
        area_id: str,
        building_code: str,
    ) -> list[dict[str, str]]:
        payload = await self._request(
            "queryFloor",
            {"areaId": area_id, "buildingCode": building_code},
        )
        return _location_rows(
            payload,
            code_fields=("floorCode", "floorId", "id", "code"),
            name_fields=("floorName", "name", "displayName"),
        )

    async def query_rooms(
        self,
        area_id: str,
        building_code: str,
        floor_code: str,
    ) -> list[dict[str, str]]:
        payload = await self._request(
            "queryRoom",
            {
                "areaId": area_id,
                "buildingCode": building_code,
                "floorCode": floor_code,
            },
        )
        return _location_rows(
            payload,
            code_fields=("roomCode", "roomId", "id", "code"),
            name_fields=("roomName", "name", "displayRoomName", "displayName"),
        )

    async def query_surplus(
        self,
        room: RoomRef,
        *,
        captured_at: int | None = None,
    ) -> Reading:
        payload = await self._request(
            "queryRoomSurplus",
            {
                "areaId": room.area_id,
                "buildingCode": room.building_code,
                "floorCode": room.floor_code,
                "roomCode": room.room_code,
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ElectricityApiError("电费接口响应缺少 data 对象。")
        reading_value = data.get("surplus", data.get("amount"))
        if reading_value is None:
            raise ElectricityApiError("电费接口响应缺少 surplus/amount 字段。")
        try:
            value: Decimal = decimal_value(reading_value, field="剩余电量")
        except ValueError as exc:
            raise ElectricityApiError(str(exc)) from exc
        balance = None
        if "amount" in data and data.get("amount") is not None:
            try:
                balance = decimal_value(data["amount"], field="余额")
            except ValueError:
                balance = None
        room_name = str(data.get("displayRoomName", "") or room.room_name).strip()
        return Reading(
            room_key=room.key,
            value=value,
            balance=balance,
            room_name=room_name,
            captured_at=int(captured_at if captured_at is not None else time.time()),
        )

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        shiro_jid = self._credentials.get("shiroJID", "")
        ym_id = self._credentials.get("ymId", "")
        if not shiro_jid:
            raise AuthExpiredError("尚未配置易校园登录态。")
        if self._session is None:
            await self.initialize()
        query = {
            "platform": "YUNMA_APP",
            **{key: str(value) for key, value in params.items()},
        }
        if ym_id:
            query["ymId"] = ym_id
        headers = {
            "Cookie": f"shiroJID={shiro_jid}",
            "User-Agent": "YUNMA_APP",
            "Accept": "application/json",
        }
        response_shiro_jid = ""
        async with self._request_lock:
            wait = self.request_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session.post(
                    f"{self.BASE_URL}/{endpoint}",
                    params=query,
                    headers=headers,
                ) as response:
                    text = await response.text()
                    response_shiro_jid = _response_cookie(
                        response,
                        "shiroJID",
                    )
                    if response.status >= 500:
                        raise ElectricityApiError(
                            f"易校园服务暂时不可用（HTTP {response.status}）。"
                        )
                    if response.status >= 400:
                        raise ElectricityApiError(
                            f"易校园请求失败（HTTP {response.status}）。"
                        )
            except asyncio.TimeoutError as exc:
                raise ElectricityApiError("易校园请求超时。") from exc
            except aiohttp.ClientError as exc:
                safe_message = _redact(
                    str(exc),
                    shiro_jid,
                    ym_id,
                )
                raise ElectricityApiError(
                    f"易校园网络请求失败：{safe_message}"
                ) from exc
            finally:
                self._last_request_at = time.monotonic()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ElectricityApiError("易校园返回了无法解析的数据。") from exc
        if not isinstance(payload, dict):
            raise ElectricityApiError("易校园返回格式不是对象。")
        if payload.get("success") is True:
            self._adopt_refreshed_cookie(
                response_shiro_jid,
                request_shiro_jid=shiro_jid,
            )
            return payload
        status_code = payload.get("statusCode")
        message = str(payload.get("message", "") or "").strip()
        if str(status_code) == "204" or "重新登录" in message:
            raise AuthExpiredError("易校园登录态已过期，请在插件管理页更新。")
        detail = (
            message
            or (f"状态码 {status_code}" if status_code is not None else "未知错误")
        )
        raise ElectricityApiError(f"易校园接口调用失败：{detail}")

    def _adopt_refreshed_cookie(
        self,
        response_shiro_jid: str,
        *,
        request_shiro_jid: str,
    ) -> bool:
        refreshed = str(response_shiro_jid or "").strip()
        if not refreshed or refreshed == request_shiro_jid:
            return False
        if self._credentials.get("shiroJID", "") != request_shiro_jid:
            return False
        self._credentials["shiroJID"] = refreshed
        self._atomic_write_credentials()
        return True

    def _load_credentials(self) -> dict[str, str]:
        if not self.credentials_path.exists():
            return {}
        try:
            raw = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            "shiroJID": str(raw.get("shiroJID", "") or "").strip(),
            "ymId": str(raw.get("ymId", "") or "").strip(),
        }

    def _atomic_write_credentials(self) -> None:
        text = json.dumps(self._credentials, ensure_ascii=False, indent=2)
        temp_path = self.credentials_path.with_name(
            f"{self.credentials_path.name}.tmp"
        )
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(self.credentials_path)
        try:
            os.chmod(self.credentials_path, 0o600)
        except OSError:
            pass


def _location_rows(
    payload: dict[str, Any],
    *,
    code_fields: tuple[str, ...],
    name_fields: tuple[str, ...],
) -> list[dict[str, str]]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ElectricityApiError("易校园位置接口响应缺少 rows 列表。")
    result = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = _first_text(raw, code_fields)
        name = _first_text(raw, name_fields)
        if not code:
            continue
        result.append({"code": code, "name": name or code})
    return result


def _first_text(data: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = data.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _mask(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:3]}{'*' * min(len(text) - 6, 12)}{text[-3:]}"


def _redact(message: str, *secrets: str) -> str:
    result = str(message or "")
    for secret in secrets:
        if secret:
            result = result.replace(str(secret), "***")
    return result


def _response_cookie(response: Any, name: str) -> str:
    cookies = getattr(response, "cookies", None)
    if not cookies:
        return ""
    morsel = cookies.get(name)
    if morsel is None:
        return ""
    return str(getattr(morsel, "value", "") or "").strip()


def _normalize_shiro_jid(value: Any) -> str:
    text = str(value or "").strip()
    if "\r" in text or "\n" in text:
        raise ValueError("shiroJID 不能包含换行。")
    if text.casefold().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        return ""
    if "=" not in text and ";" not in text:
        return text
    parsed = SimpleCookie()
    try:
        parsed.load(text)
    except Exception as exc:
        raise ValueError("无法解析 shiroJID Cookie。") from exc
    for name, morsel in parsed.items():
        if name.casefold() == "shirojid":
            return str(morsel.value or "").strip()
    raise ValueError("Cookie 文本中未找到 shiroJID。")
