from __future__ import annotations

import unittest
from decimal import Decimal

from core.models import RoomRef, decimal_text, normalize_subscription


class ModelTests(unittest.TestCase):
    def test_decimal_text_preserves_exact_decimal(self):
        self.assertEqual(decimal_text("20.500"), "20.5")
        self.assertEqual(decimal_text(Decimal("0.0100")), "0.01")

    def test_invalid_decimal_is_rejected(self):
        for value in ("", "abc", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                decimal_text(value)

    def test_room_key_is_stable_and_ignores_display_names(self):
        first = RoomRef(
            area_id="a",
            area_name="一校区",
            building_code="b",
            building_name="六号楼",
            floor_code="2",
            floor_name="二层",
            room_code="240",
            room_name="240",
        )
        second = RoomRef(
            area_id="a",
            area_name="校区改名",
            building_code="b",
            building_name="楼栋改名",
            floor_code="2",
            floor_name="楼层改名",
            room_code="240",
            room_name="房间改名",
        )
        self.assertEqual(first.key, second.key)

    def test_subscription_bounds(self):
        normalized = normalize_subscription(
            {
                "alias": "寝室",
                "threshold": "20.00",
                "interval_seconds": 300,
                "unit": "度",
                "enabled": True,
            }
        )
        self.assertEqual(normalized["threshold"], "20")
        with self.assertRaises(ValueError):
            normalize_subscription(
                {
                    "alias": "寝室",
                    "threshold": "20",
                    "interval_seconds": 299,
                }
            )


if __name__ == "__main__":
    unittest.main()

