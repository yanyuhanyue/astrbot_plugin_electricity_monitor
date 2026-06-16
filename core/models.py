"""Core data models and validation helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_value(value: Any, *, field: str = "数值") -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field}不是有效数字。") from exc
    if not result.is_finite():
        raise ValueError(f"{field}不是有限数字。")
    return result


def decimal_text(value: Any, *, field: str = "数值") -> str:
    result = decimal_value(value, field=field)
    text = format(result.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def display_decimal(value: Any) -> str:
    return decimal_text(value)


@dataclass(frozen=True, slots=True)
class RoomRef:
    area_id: str
    area_name: str
    building_code: str
    building_name: str
    floor_code: str
    floor_name: str
    room_code: str
    room_name: str

    @property
    def key(self) -> str:
        payload = json.dumps(
            [
                self.area_id,
                self.building_code,
                self.floor_code,
                self.room_code,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    @property
    def display_name(self) -> str:
        parts = [
            self.area_name,
            self.building_name,
            self.floor_name,
            self.room_name,
        ]
        return " / ".join(item for item in parts if item)

    def to_dict(self) -> dict[str, str]:
        result = asdict(self)
        result["room_key"] = self.key
        result["display_name"] = self.display_name
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoomRef":
        if not isinstance(data, dict):
            raise ValueError("寝室信息必须是对象。")
        values = {}
        for field in (
            "area_id",
            "area_name",
            "building_code",
            "building_name",
            "floor_code",
            "floor_name",
            "room_code",
            "room_name",
        ):
            values[field] = str(data.get(field, "") or "").strip()
        for field in ("area_id", "building_code", "floor_code", "room_code"):
            if not values[field]:
                raise ValueError(f"寝室信息缺少 {field}。")
        return cls(**values)


@dataclass(frozen=True, slots=True)
class Reading:
    room_key: str
    value: Decimal
    balance: Decimal | None
    room_name: str
    captured_at: int

    def to_dict(self) -> dict[str, Any]:
        balance = decimal_text(self.balance) if self.balance is not None else ""
        return {
            "room_key": self.room_key,
            "value": decimal_text(self.value),
            "balance": balance,
            "room_name": self.room_name,
            "captured_at": int(self.captured_at),
        }


def normalize_subscription(data: dict[str, Any] | None) -> dict[str, Any]:
    raw = data if isinstance(data, dict) else {}
    alias = str(raw.get("alias", "") or "").strip()
    if not alias:
        raise ValueError("订阅别名不能为空。")
    if len(alias) > 40:
        raise ValueError("订阅别名不能超过 40 个字符。")
    unit = str(raw.get("unit", "度") or "度").strip()
    if not unit or len(unit) > 12:
        raise ValueError("单位长度应为 1–12 个字符。")
    try:
        interval_seconds = int(raw.get("interval_seconds", 900))
    except (TypeError, ValueError) as exc:
        raise ValueError("查询频率不是有效整数。") from exc
    if interval_seconds < 300 or interval_seconds > 86_400:
        raise ValueError("查询频率必须在 5–1440 分钟之间。")
    return {
        "alias": alias,
        "unit": unit,
        "threshold": decimal_text(raw.get("threshold", "20"), field="告警阈值"),
        "interval_seconds": interval_seconds,
        "enabled": bool(raw.get("enabled", True)),
    }
