"""Gemini helper-logic tests — pure functions only, NO network / NO quota burn.

Covers JSON-fence stripping, price-text extraction, and the model fallback
ordering. The actual HTTP calls are never exercised here.

Run:  python -m unittest tests.test_gemini   (from project root)
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server  # noqa: E402  (path set above)


class ParseGeminiJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(server._parse_gemini_json('{"a": 1}'), {"a": 1})

    def test_strips_markdown_fence(self):
        text = '```json\n{"year": 2025, "components": []}\n```'
        self.assertEqual(server._parse_gemini_json(text)["year"], 2025)

    def test_strips_bare_fence(self):
        self.assertEqual(server._parse_gemini_json('```\n{"x": true}\n```'), {"x": True})

    def test_invalid_raises(self):
        with self.assertRaises(Exception):
            server._parse_gemini_json("not json at all")


class ParsePriceText(unittest.TestCase):
    def test_plain_integer(self):
        self.assertEqual(server._parse_price_text("1500000"), 1500000.0)

    def test_strips_commas(self):
        self.assertEqual(server._parse_price_text("18,65,000"), 1865000.0)

    def test_decimal(self):
        self.assertEqual(server._parse_price_text("7200.50"), 7200.50)

    def test_null_word_is_none(self):
        self.assertIsNone(server._parse_price_text("null"))
        self.assertIsNone(server._parse_price_text("None"))
        self.assertIsNone(server._parse_price_text(""))

    def test_extracts_first_number_from_noise(self):
        self.assertEqual(server._parse_price_text("Approximately 4800000 INR"), 4800000.0)

    def test_no_number_is_none(self):
        self.assertIsNone(server._parse_price_text("price unavailable"))


class PickModels(unittest.TestCase):
    def setUp(self):
        self._orig = server._gemini_available_models

    def tearDown(self):
        server._gemini_available_models = self._orig

    def test_preferred_order_kept_and_filtered(self):
        # only these are "available" — picker keeps preference order, drops the rest
        server._gemini_available_models = {"gemini-2.5-flash", "gemini-2.0-flash", "some-other-model"}
        picked = server._pick_gemini_models()
        self.assertEqual(picked[0], "gemini-2.0-flash")          # most-preferred available first
        self.assertIn("gemini-2.5-flash", picked)
        self.assertLess(picked.index("gemini-2.0-flash"), picked.index("gemini-2.5-flash"))
        self.assertNotIn("some-other-model", picked)             # not flash/pro/vision -> excluded

    def test_appends_unknown_flash_models_as_extras(self):
        server._gemini_available_models = {"gemini-2.0-flash", "gemini-9.9-flash-experimental"}
        picked = server._pick_gemini_models()
        self.assertIn("gemini-9.9-flash-experimental", picked)   # flash extra kept
        self.assertEqual(picked[0], "gemini-2.0-flash")

    def test_falls_back_to_full_list_when_listing_fails(self):
        server._gemini_available_models = set()                  # listing returned nothing
        self.assertEqual(server._pick_gemini_models(), server.GEMINI_PREFERRED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
