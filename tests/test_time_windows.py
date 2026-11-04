from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from monitoring_bot_v3.time_windows import (
    IntervalParseError,
    lookback_interval,
    parse_exact_interval,
)


TZ = ZoneInfo("Asia/Yekaterinburg")


class ExactIntervalTests(unittest.TestCase):
    def test_all_agreed_formats(self):
        values = (
            "10.10.2026 15:00 - 11.10.2026 16:00",
            "10.10.2026 1500 - 11.10.2026 1600",
            "10102026 1500 11102026 1600",
            "101020261500-111020261600",
            "10.10.26 15:00 - 11.10.26 16:00",
        )
        for value in values:
            with self.subTest(value=value):
                interval = parse_exact_interval(value)
                self.assertEqual(datetime(2026, 10, 10, 15, tzinfo=TZ), interval.start)
                self.assertEqual(datetime(2026, 10, 11, 16, tzinfo=TZ), interval.end)

    def test_date_only_formats_cover_full_final_day(self):
        for value in ("10.10.2026 - 11.10.2026", "10102026-11102026"):
            with self.subTest(value=value):
                interval = parse_exact_interval(value)
                self.assertEqual(datetime(2026, 10, 10, tzinfo=TZ), interval.start)
                self.assertEqual(datetime(2026, 10, 11, 23, 59, 59, 999999, tzinfo=TZ), interval.end)

    def test_interval_across_midnight(self):
        interval = parse_exact_interval(
            "29.10.2026 23:59 - 30.10.2026 00:01"
        )
        self.assertEqual(120, (interval.end - interval.start).total_seconds())

    def test_boundaries_are_inclusive(self):
        interval = parse_exact_interval(
            "29.10.2026 15:00 - 29.10.2026 16:00"
        )
        self.assertTrue(interval.contains(interval.start))
        self.assertTrue(interval.contains(interval.end))

    def test_timezone_is_yekaterinburg(self):
        interval = parse_exact_interval(
            "29.10.2026 15:00 - 29.10.2026 16:00"
        )
        self.assertEqual(5 * 3600, interval.start.utcoffset().total_seconds())

    def test_invalid_date(self):
        with self.assertRaisesRegex(IntervalParseError, "Некорректная"):
            parse_exact_interval("31.02.2026 15:00 - 01.03.2026 16:00")

    def test_invalid_time(self):
        with self.assertRaisesRegex(IntervalParseError, "Некорректная"):
            parse_exact_interval("29.10.2026 25:00 - 30.10.2026 03:00")

    def test_start_must_precede_end(self):
        with self.assertRaisesRegex(IntervalParseError, "раньше"):
            parse_exact_interval("30.10.2026 03:00 - 29.10.2026 15:00")

    def test_unrecognized_format_has_example(self):
        with self.assertRaisesRegex(IntervalParseError, "Пример"):
            parse_exact_interval("завтра вечером")


class LookbackTests(unittest.TestCase):
    def test_default_style_window(self):
        now = datetime(2026, 10, 29, 12, 30, tzinfo=TZ)
        interval = lookback_interval(12, now=now)
        self.assertEqual(datetime(2026, 10, 29, 0, 30, tzinfo=TZ), interval.start)
        self.assertEqual(now, interval.end)

    def test_positive_integer_required(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(IntervalParseError):
                    lookback_interval(value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

