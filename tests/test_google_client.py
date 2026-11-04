from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from monitoring_bot_v3.google_client import (
    AppsScriptClient,
    AppsScriptError,
    parse_google_datetime,
)
from monitoring_bot_v3.models import TimeInterval


TZ = ZoneInfo("Asia/Yekaterinburg")


class FakeResponse:
    def __init__(self, payload=None, status_code=200, json_error=False):
        self.payload = payload
        self.status_code = status_code
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("bad json")
        return self.payload


def interval():
    return TimeInterval(
        datetime(2026, 10, 29, 15, tzinfo=TZ),
        datetime(2026, 10, 29, 16, tzinfo=TZ),
    )


def record(news_datetime="2026-10-29T10:30:00.000Z"):
    return {
        "analysis_id": "analysis-1",
        "object_number": "1",
        "model": "Gemini + GigaChat",
        "name": "Склад",
        "object_type": "склад",
        "organization": "Организация",
        "region": "Свердловская область",
        "locality": "Екатеринбург",
        "address": "ул. Примерная, 1",
        "cadastral_number": "66:41:0000000:1",
        "damage": "не установлены",
        "evidence_fragment": "фрагмент",
        "ai_note": "",
        "news_datetime": news_datetime,
        "cadastral_quarter": "66:41:0000000",
        "all_cadastral_numbers": ["66:41:0000000:1", "66:41:0000000:2"],
        "gemini": "",
        "analysis": {
            "analysis_id": "analysis-1",
            "incident_type": "пожар",
            "source_url": "https://example.test/news",
            "status": "Готово",
            "summary": "Краткое описание",
        },
    }


class GoogleDateTests(unittest.TestCase):
    def test_display_string(self):
        value = parse_google_datetime("29.10.2026 15:30:00", TZ)
        self.assertEqual(datetime(2026, 10, 29, 15, 30, tzinfo=TZ), value)

    def test_iso_is_converted_to_local_timezone(self):
        value = parse_google_datetime("2026-10-29T10:30:00Z", TZ)
        self.assertEqual(datetime(2026, 10, 29, 15, 30, tzinfo=TZ), value)

    def test_google_serial(self):
        expected = datetime(2026, 10, 29, 15, 30, tzinfo=TZ)
        base = datetime(1899, 12, 30, tzinfo=TZ)
        serial = (expected - base).total_seconds() / 86400
        self.assertEqual(expected, parse_google_datetime(serial, TZ))


class AppsScriptClientTests(unittest.TestCase):
    def client(self, post, attempts=1):
        return AppsScriptClient(
            "https://script.google.com/macros/s/test/exec",
            "secret",
            post=post,
            attempts=attempts,
            sleep=lambda _: None,
        )

    def test_objects_and_analysis_are_joined(self):
        client = self.client(
            lambda *args, **kwargs: FakeResponse({"ok": True, "records": [record()]})
        )
        rows = client.read_monitoring_data(interval())
        self.assertEqual(1, len(rows))
        self.assertEqual("analysis-1", rows[0].object.analysis_id)
        self.assertEqual("пожар", rows[0].analysis.incident_type)
        self.assertEqual(2, len(rows[0].object.all_cadastral_numbers))

    def test_filter_uses_news_datetime_and_inclusive_boundaries(self):
        rows = [
            record("2026-10-29T10:00:00Z"),
            record("2026-10-29T11:00:00Z"),
            record("2026-10-29T11:00:01Z"),
        ]
        client = self.client(
            lambda *args, **kwargs: FakeResponse({"ok": True, "records": rows})
        )
        self.assertEqual(2, len(client.read_monitoring_data(interval())))

    def test_multiple_objects_for_one_analysis_are_preserved(self):
        second = {**record(), "object_number": "2", "name": "Цех"}
        client = self.client(
            lambda *args, **kwargs: FakeResponse(
                {"ok": True, "records": [record(), second]}
            )
        )
        self.assertEqual(2, len(client.read_monitoring_data(interval())))

    def test_recipient_methods(self):
        responses = iter(
            [
                FakeResponse({"ok": True, "recipients": ["A@Example.com", "a@example.com"]}),
                FakeResponse({"ok": True, "status": "added"}),
                FakeResponse({"ok": True, "status": "deleted"}),
            ]
        )
        client = self.client(lambda *args, **kwargs: next(responses))
        self.assertEqual(["a@example.com"], client.read_recipients())
        self.assertEqual("added", client.add_recipient("b@example.com"))
        self.assertEqual("deleted", client.delete_recipient("b@example.com"))

    def test_schedule_methods_are_independent_and_sorted(self):
        responses = iter([
            FakeResponse({"ok": True, "times": ["19:00", "08:00", "13:00"]}),
            FakeResponse({"ok": True, "status": "added"}),
            FakeResponse({"ok": True, "status": "deleted"}),
        ])
        client = self.client(lambda *args, **kwargs: next(responses))
        self.assertEqual(["08:00", "13:00", "19:00"], client.read_schedule_times())
        self.assertEqual("added", client.add_schedule_time("09:30"))
        self.assertEqual("deleted", client.delete_schedule_time("09:30"))

    def test_schedule_normalizes_text_and_formatted_time_displays(self):
        client = self.client(lambda *args, **kwargs: FakeResponse({
            "ok": True, "times": ["8:00", "08:00", "10:00", "15:00"]
        }))
        self.assertEqual(["08:00", "10:00", "15:00"], client.read_schedule_times())

    def test_invalid_schedule_time_is_rejected_before_http(self):
        calls = []
        client = self.client(lambda *args, **kwargs: calls.append(1))
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            client.add_schedule_time("25:00")
        self.assertEqual([], calls)

    def test_http_error(self):
        client = self.client(lambda *args, **kwargs: FakeResponse(status_code=403))
        with self.assertRaisesRegex(AppsScriptError, "403"):
            client.read_recipients()

    def test_invalid_json(self):
        client = self.client(
            lambda *args, **kwargs: FakeResponse(json_error=True), attempts=1
        )
        with self.assertRaisesRegex(AppsScriptError, "JSON"):
            client.read_recipients()

    def test_retryable_connection_error_is_bounded(self):
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            raise requests.ConnectionError("offline")

        client = self.client(failing, attempts=3)
        with self.assertRaises(AppsScriptError):
            client.read_recipients()
        self.assertEqual(3, len(calls))

    def test_server_rejection(self):
        client = self.client(
            lambda *args, **kwargs: FakeResponse(
                {"ok": False, "error": "unauthorized"}
            )
        )
        with self.assertRaisesRegex(AppsScriptError, "unauthorized"):
            client.read_recipients()


if __name__ == "__main__":
    unittest.main()

