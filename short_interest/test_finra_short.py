"""Tests with small made-up inputs in FINRA's formats.

Run with:  python -m unittest test_finra_short.py
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

import finra_short as fs


def raw_rows(day, symbol, short, adv=1_000_000, market="NNM", split=None):
    return {
        "settlementDate": day, "symbolCode": symbol, "issueName": f"{symbol} Inc",
        "marketClassCode": market, "currentShortPositionQuantity": short,
        "averageDailyVolumeQuantity": adv, "daysToCoverQuantity": short / adv,
        "stockSplitFlag": split,
    }


class ShortInterestTests(unittest.TestCase):
    def setUp(self):
        days = ["2026-09-15", "2026-09-30", "2026-10-15"]
        rows = []
        for d, up, down, wobble in zip(days, [2e6, 3e6, 4e6], [5e6, 4e6, 3e6], [2e6, 1.5e6, 3e6]):
            rows.append(raw_rows(d, "UP", up))
            rows.append(raw_rows(d, "DOWN", down))
            rows.append(raw_rows(d, "WOBBLE", wobble))
            rows.append(raw_rows(d, "TINY", 10_000))
            rows.append(raw_rows(d, "PENNY", up * 10, market="OTC"))
        rows.append(raw_rows("2026-10-15", "SPLIT", 9e6, split="Y"))
        rows.append(raw_rows("2026-09-15", "SPLIT", 1e6))
        self.si = fs.tidy_short_interest(pd.DataFrame(rows))

    def test_changes_and_filters(self):
        table = fs.analyze_short_interest(self.si, 1_000_000, 100_000, ["OTC"]).set_index("symbol")
        self.assertEqual(sorted(table.index), ["DOWN", "UP", "WOBBLE"])
        self.assertEqual(table.loc["UP", "change_shares"], 2e6)
        self.assertAlmostEqual(table.loc["UP", "change_pct"], 100.0)
        self.assertAlmostEqual(table.loc["DOWN", "change_pct"], -40.0)
        self.assertTrue(table.loc["UP", "rising_every_report"])
        self.assertFalse(table.loc["WOBBLE", "rising_every_report"])
        self.assertAlmostEqual(table.loc["UP", "days_to_cover_change"], 2.0)

    def test_report_renders(self):
        table = fs.analyze_short_interest(self.si, 1_000_000, 100_000, ["OTC"])
        dates = sorted(self.si["date"].unique())
        report = fs.build_report(table, pd.DataFrame(), dates, None, 10)
        self.assertIn("| UP | UP Inc | 2,000,000 | 3,000,000 | 4,000,000 | 2,000,000 | 100.0 |", report)


class ShortVolumeTests(unittest.TestCase):
    def test_parse_and_trend(self):
        files = {
            "20261001": [("RISE", 300, 1000), ("FLAT", 500, 1000)],
            "20261002": [("RISE", 300, 1000), ("FLAT", 500, 1000)],
            "20261005": [("RISE", 700, 1000), ("FLAT", 500, 1000)],
            "20261006": [("RISE", 700, 1000), ("FLAT", 500, 1000)],
        }
        frames = []
        for day, recs in files.items():
            text = "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
            text += "".join(f"{day}|{s}|{sv}|0|{tv}|B,Q,N\n" for s, sv, tv in recs)
            text += "2\n"  # trailer line some files carry
            frames.append(fs.parse_short_volume(text))
        sv = pd.concat(frames, ignore_index=True)
        self.assertEqual(len(sv), 8)

        table = fs.analyze_short_volume(sv, 100).set_index("symbol")
        self.assertAlmostEqual(table.loc["RISE", "short_ratio_early"], 30.0)
        self.assertAlmostEqual(table.loc["RISE", "short_ratio_late"], 70.0)
        self.assertAlmostEqual(table.loc["RISE", "short_ratio_change_pts"], 40.0)
        self.assertAlmostEqual(table.loc["FLAT", "short_ratio_change_pts"], 0.0)


class FakeResponse:
    def __init__(self, rows):
        self.rows = rows
        self.text = "x" if rows else ""

    def raise_for_status(self):
        pass

    def json(self):
        return self.rows


class FakeSession:
    """Serves 12,000 rows in pages, like FINRA's API."""

    def __init__(self):
        self.rows = [raw_rows("2026-10-15", f"S{i}", 1e6) for i in range(12_000)]
        self.bodies = []

    def post(self, url, json, timeout):
        self.bodies.append(json)
        return FakeResponse(self.rows[json["offset"]:json["offset"] + json["limit"]])


class FetchTests(unittest.TestCase):
    def test_pagination_and_cache(self):
        session = FakeSession()
        with tempfile.TemporaryDirectory() as tmp:
            df = fs.fetch_short_interest(session, date(2026, 10, 15), Path(tmp))
            self.assertEqual(len(df), 12_000)
            self.assertEqual([b["offset"] for b in session.bodies], [0, 5000, 10000])
            # Second call is served from the cache file.
            again = fs.fetch_short_interest(session, date(2026, 10, 15), Path(tmp))
            self.assertEqual(len(again), 12_000)
            self.assertEqual(len(session.bodies), 3)


if __name__ == "__main__":
    unittest.main()
