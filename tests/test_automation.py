from __future__ import annotations

import tempfile
import unittest
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring_bot_v3.automation import (
    StateStore,
    automation_enable_error,
    next_scheduled_run,
    normalize_schedule,
    normalize_schedule_time,
)
from monitoring_bot_v3.telegram_bot import TelegramController
from tests.test_delivery import settings
from unittest.mock import AsyncMock, patch


TZ = ZoneInfo("Asia/Yekaterinburg")


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Корутина неожиданно приостановилась")


async def direct_to_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


class AutomationTests(unittest.TestCase):
    def test_schedule_validation_sorting_and_duplicates(self):
        self.assertEqual(["08:00", "13:00", "19:00"], normalize_schedule(["19:00", "08:00", "13:00", "08:00"]))
        self.assertEqual("08:00", normalize_schedule_time("8:00"))
        self.assertEqual("08:00", normalize_schedule_time("08:00"))
        for value in ("24:00", "12:60", "noon"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_schedule_time(value)

    def test_next_run_uses_project_timezone_and_next_day(self):
        now = datetime(2026, 10, 29, 13, 1, tzinfo=TZ)
        same_day = next_scheduled_run(["08:00", "19:00"], now)
        self.assertEqual(datetime(2026, 10, 29, 19, tzinfo=TZ), same_day)
        next_day = next_scheduled_run(["08:00"], now)
        self.assertEqual(datetime(2026, 10, 30, 8, tzinfo=TZ), next_day)

    def test_empty_schedule_has_no_next_run(self):
        self.assertIsNone(next_scheduled_run([], datetime(2026, 10, 29, tzinfo=TZ)))

    def test_state_defaults_disabled_and_survives_restart(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "state.json"
            store = StateStore(path)
            self.assertFalse(store.get().enabled)
            store.update(enabled=True, last_email_result="Успешно")
            restored = StateStore(path).get()
            self.assertTrue(restored.enabled)
            self.assertEqual("Успешно", restored.last_email_result)
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("password", content.casefold())
            self.assertNotIn("token", content.casefold())

    def test_one_scheduler_trigger_per_minute(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            controller = TelegramController(
                settings(automation_state_file=Path(directory) / "state.json"),
                logging.getLogger("scheduler-test"),
            )
            controller.store.update(enabled=True)
            controller._client = lambda: type("Client", (), {"read_schedule_times": lambda self: ["08:00"]})()
            controller.run_automatic = AsyncMock()
            moment = datetime(2026, 10, 29, 8, 0, 10, tzinfo=TZ)
            with patch("monitoring_bot_v3.telegram_bot.asyncio.to_thread", new=direct_to_thread):
                self.assertTrue(run_immediate(controller.scheduler_tick(moment)))
                self.assertFalse(run_immediate(controller.scheduler_tick(moment.replace(second=50))))
            controller.run_automatic.assert_awaited_once()

    def test_monitoring_error_prevents_smtp(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            controller = TelegramController(
                settings(automation_state_file=Path(directory) / "state.json"),
                logging.getLogger("scheduler-test"),
            )
            with (
                patch("monitoring_bot_v3.telegram_bot.asyncio.to_thread", new=direct_to_thread),
                patch("monitoring_bot_v3.telegram_bot.generate_report_bundle", side_effect=RuntimeError("source failed")),
                patch("monitoring_bot_v3.telegram_bot.send_email_report") as send,
            ):
                run_immediate(controller.run_automatic())
            send.assert_not_called()
            self.assertIn("Ошибка", controller.store.get().last_monitoring_result)

    def test_automation_requires_recipients_and_times(self):
        self.assertEqual("нет получателей; не настроено время отправки", automation_enable_error([], []))
        self.assertEqual("нет получателей", automation_enable_error([], ["08:00"]))
        self.assertEqual("не настроено время отправки", automation_enable_error(["a@test.ru"], []))
        self.assertIsNone(automation_enable_error(["a@test.ru"], ["08:00"]))


if __name__ == "__main__":
    unittest.main()
