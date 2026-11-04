from __future__ import annotations

import logging
import asyncio
import tempfile
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from monitoring_bot_v3.config import ConfigurationError
from monitoring_bot_v3.telegram_bot import (
    STATE_SECRET,
    TelegramController,
    format_user_datetime,
    main_menu,
)
from tests.test_delivery import settings


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.edited_texts = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.edited_texts.append((text, kwargs))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.answers = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def message_update(text=""):
    message = FakeMessage(text)
    return SimpleNamespace(
        message=message, effective_message=message, callback_query=None
    )


def callback_update(data):
    query = FakeQuery(data)
    return SimpleNamespace(
        message=None, effective_message=query.message, callback_query=query
    )


def context():
    return SimpleNamespace(user_data={})


def run_immediate(coroutine):
    """Драйвер для обработчиков с синхронными fake-await без event loop."""
    try:
        coroutine.send(None)
    except StopIteration:
        return
    raise AssertionError("Тестовый обработчик неожиданно приостановился")


class TelegramAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.controller = TelegramController(settings(), logging.getLogger("auth-test"))

    def test_successful_authorization_and_repeat_start(self):
        ctx = context()
        start = message_update()
        run_immediate(self.controller.start(start, ctx))
        self.assertEqual(STATE_SECRET, ctx.user_data["state"])
        self.assertIn("секретное слово", start.message.replies[-1][0].lower())

        secret = message_update("correct horse")
        run_immediate(self.controller.text(secret, ctx))
        self.assertTrue(ctx.user_data["authorized"])
        self.assertNotIn("state", ctx.user_data)
        self.assertIn("Доступ разрешён", secret.message.replies[0][0])

        repeated = message_update()
        run_immediate(self.controller.start(repeated, ctx))
        self.assertTrue(ctx.user_data["authorized"])
        self.assertIn("Главное меню", repeated.message.replies[-1][0])

    def test_wrong_secret_is_rejected_and_can_be_retried(self):
        ctx = context()
        run_immediate(self.controller.start(message_update(), ctx))
        wrong = message_update("wrong")
        run_immediate(self.controller.text(wrong, ctx))
        self.assertFalse(ctx.user_data.get("authorized", False))
        self.assertEqual(STATE_SECRET, ctx.user_data["state"])
        self.assertIn("Неверное", wrong.message.replies[-1][0])

        correct = message_update("correct horse")
        run_immediate(self.controller.text(correct, ctx))
        self.assertTrue(ctx.user_data["authorized"])

    def test_cyrillic_secret_authorizes_in_telegram_flow(self):
        controller = TelegramController(
            settings(telegram_secret_word="Секретное слово"),
            logging.getLogger("auth-test-unicode"),
        )
        ctx = context()
        run_immediate(controller.start(message_update(), ctx))
        reply = message_update("Секретное слово")
        run_immediate(controller.text(reply, ctx))
        self.assertTrue(ctx.user_data["authorized"])
        self.assertIn("Доступ разрешён", reply.message.replies[0][0])

    def test_authorization_is_isolated_per_user_context(self):
        first, second = context(), context()
        run_immediate(self.controller.start(message_update(), first))
        run_immediate(self.controller.text(message_update("correct horse"), first))
        run_immediate(self.controller.start(message_update(), second))
        self.assertTrue(first.user_data["authorized"])
        self.assertFalse(second.user_data.get("authorized", False))
        self.assertEqual(STATE_SECRET, second.user_data["state"])

    def test_all_callbacks_are_blocked_without_authorization(self):
        self.controller._generate = AsyncMock()
        for action in ("monitoring", "last12", "interval", "email_settings", "recipients_list", "recipient_add", "schedule", "toggle_automation", "status", "channel_test"):
            update = callback_update(action)
            run_immediate(self.controller.callback(update, context()))
            self.assertTrue(update.callback_query.answers)
            self.assertIn("авторизуйтесь", update.callback_query.answers[0][0])
        self.controller._generate.assert_not_awaited()

    def test_main_and_monitoring_menus_have_new_navigation(self):
        ctx = context()
        ctx.user_data["authorized"] = True
        main = callback_update("main")
        run_immediate(self.controller.callback(main, ctx))
        labels = [button.text for row in main.callback_query.edits[-1][1]["reply_markup"].inline_keyboard for button in row]
        self.assertEqual(["🔎 Сделать мониторинг", "✉️ Настроить письма", "📊 Состояние приложения"], labels)

        monitoring = callback_update("monitoring")
        run_immediate(self.controller.callback(monitoring, ctx))
        labels = [button.text for row in monitoring.callback_query.edits[-1][1]["reply_markup"].inline_keyboard for button in row]
        self.assertEqual(["🕒 Последние 12 часов", "📅 Выбрать интервал", "⬅️ Назад"], labels)

    def test_manual_monitoring_only_generates_without_email_flow(self):
        ctx = context()
        ctx.user_data["authorized"] = True
        self.controller._generate = AsyncMock()
        update = callback_update("last12")
        run_immediate(self.controller.callback(update, ctx))
        self.controller._generate.assert_awaited_once()
        self.assertFalse(any("send" in (button.callback_data or "") for row in main_menu().inline_keyboard for button in row))

    def test_wait_notice_is_sent_once_after_delay(self):
        message = FakeMessage()
        with patch("monitoring_bot_v3.telegram_bot.asyncio.sleep", new=AsyncMock()) as sleep:
            run_immediate(self.controller._wait_notice(message))
        sleep.assert_awaited_once_with(15)
        self.assertEqual(0, len(message.replies))
        self.assertEqual(1, len(message.edited_texts))
        self.assertIn("Я не завис", message.edited_texts[0][0])

    def test_cancelled_wait_never_overwrites_final_status(self):
        class PendingSleep:
            def __await__(self):
                yield "pending"

        message = FakeMessage()
        with patch("monitoring_bot_v3.telegram_bot.asyncio.sleep", new=lambda _: PendingSleep()):
            coroutine = self.controller._wait_notice(message)
            coroutine.send(None)
            with self.assertRaises(asyncio.CancelledError):
                coroutine.throw(asyncio.CancelledError())
        self.assertEqual([], message.edited_texts)

    def test_progress_edit_error_is_contained(self):
        class FailingProgress:
            async def edit_text(self, text):
                raise RuntimeError("telegram offline")

        self.assertFalse(run_immediate(self.controller._edit_progress(FailingProgress(), "status")))

    def test_status_datetime_is_human_readable_in_yekaterinburg(self):
        self.assertEqual(
            "29.10.2026 13:21",
            format_user_datetime("2026-10-29T08:21:26+00:00", "Asia/Yekaterinburg"),
        )
        self.assertEqual(
            "Ещё не выполнялось",
            format_user_datetime(None, "Asia/Yekaterinburg"),
        )

    def test_status_button_and_command_use_same_renderer(self):
        ctx = context()
        ctx.user_data["authorized"] = True
        self.controller._show_status = AsyncMock()
        callback = callback_update("status")
        run_immediate(self.controller.callback(callback, ctx))
        command = message_update()
        run_immediate(self.controller.status(command, ctx))
        self.assertEqual(2, self.controller._show_status.await_count)

    def test_automation_is_enabled_only_with_valid_saved_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            controller = TelegramController(
                settings(automation_state_file=Path(directory) / "state.json"),
                logging.getLogger("toggle-test"),
            )
            controller._show_email_settings = AsyncMock()
            controller._read_email_settings = AsyncMock(return_value=([], ["08:00"]))
            ctx = context()
            ctx.user_data["authorized"] = True
            rejected = callback_update("toggle_automation")
            run_immediate(controller.callback(rejected, ctx))
            self.assertFalse(controller.store.get().enabled)
            self.assertIn("не включена", rejected.callback_query.message.replies[-1][0])

            controller._read_email_settings = AsyncMock(
                return_value=(["a@example.test"], ["08:00"])
            )
            accepted = callback_update("toggle_automation")
            run_immediate(controller.callback(accepted, ctx))
            self.assertTrue(controller.store.get().enabled)
            self.assertIn("включена", accepted.callback_query.message.replies[-1][0])

    def test_text_without_start_cannot_trigger_interval_action(self):
        ctx = context()
        update = message_update("22.10.2026 00:00 - 29.10.2026 09:30")
        run_immediate(self.controller.text(update, ctx))
        self.assertFalse(ctx.user_data.get("authorized", False))
        self.assertEqual(STATE_SECRET, ctx.user_data["state"])

    def test_return_to_menu_preserves_authorization(self):
        ctx = context()
        run_immediate(self.controller.start(message_update(), ctx))
        run_immediate(self.controller.text(message_update("correct horse"), ctx))
        ctx.user_data["temporary"] = "value"
        update = callback_update("menu")
        run_immediate(self.controller.callback(update, ctx))
        self.assertTrue(ctx.user_data["authorized"])
        self.assertNotIn("temporary", ctx.user_data)
        self.assertTrue(update.callback_query.edits)

    def test_missing_secret_blocks_bot_startup(self):
        with self.assertRaisesRegex(ConfigurationError, "TELEGRAM_SECRET_WORD"):
            settings(telegram_secret_word="").validate_telegram()


if __name__ == "__main__":
    unittest.main()
