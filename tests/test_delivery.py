from __future__ import annotations

import unittest
from pathlib import Path

from monitoring_bot_v3.config import Settings
from monitoring_bot_v3.email_delivery import (
    ConfirmationRequiredError,
    DeliveryError,
    DeliveryGuard,
    DuplicateDeliveryError,
    build_email_message,
    send_email_report,
)
from monitoring_bot_v3.telegram_bot import (
    TelegramController,
    split_telegram_text,
)


def settings(**overrides):
    values = dict(
        telegram_bot_token="token",
        telegram_secret_word="correct horse",
        google_apps_script_url="https://script.google.com/macros/s/test/exec",
        google_apps_script_api_key="key",
        google_http_timeout_seconds=30,
        google_http_attempts=1,
        collateral_excel_path=Path("registry.xlsx"),
        lookback_hours=12,
        timezone_name="Asia/Yekaterinburg",
        log_file=Path("test.log"),
        email_smtp_host="smtp.test",
        email_smtp_port=465,
        email_login="sender@example.test",
        email_password="secret",
        email_timeout_seconds=30,
    )
    values.update(overrides)
    return Settings(**values)


class FakeSmtp:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.login_args = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def login(self, login, password):
        self.login_args = (login, password)

    def send_message(self, message):
        self.message = message


class FailingSmtp(FakeSmtp):
    def login(self, login, password):
        raise OSError("offline")


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        FakeSmtp.instances.clear()

    def test_email_contains_text_selected_recipients_and_date(self):
        message = build_email_message(
            report_text="Отчёт", recipients=["A@example.test", "b@example.test"],
            sender="sender@example.test", report_date="29.10.2026",
        )
        self.assertEqual("a@example.test, b@example.test", message["To"])
        self.assertIn("29.10.2026", message["Subject"])
        self.assertIn("Отчёт", message.get_content())

    def test_smtp_is_mocked_and_receives_no_real_send(self):
        sent = send_email_report(
            settings(), report_text="Текст", recipients=["a@example.test"],
            report_date="29.10.2026", smtp_factory=FakeSmtp,
        )
        self.assertEqual(("a@example.test",), sent)
        self.assertEqual(("sender@example.test", "secret"), FakeSmtp.instances[0].login_args)

    def test_missing_settings_and_empty_recipients_are_errors(self):
        with self.assertRaises(DeliveryError):
            build_email_message(report_text="x", recipients=[], sender="s", report_date="d")
        with self.assertRaisesRegex(ValueError, "EMAIL_LOGIN"):
            send_email_report(settings(email_login=""), report_text="x", recipients=["a@b.test"], report_date="d", smtp_factory=FakeSmtp)

    def test_confirmation_and_duplicate_protection(self):
        guard = DeliveryGuard()
        calls = []
        with self.assertRaises(ConfirmationRequiredError):
            guard.run_once("one", confirmed=False, action=lambda: calls.append(1))
        guard.run_once("one", confirmed=True, action=lambda: calls.append(1))
        with self.assertRaises(DuplicateDeliveryError):
            guard.run_once("one", confirmed=True, action=lambda: calls.append(2))
        self.assertEqual([1], calls)

    def test_failed_delivery_can_be_retried(self):
        guard = DeliveryGuard()
        with self.assertRaises(RuntimeError):
            guard.run_once("one", confirmed=True, action=lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        self.assertEqual("ok", guard.run_once("one", confirmed=True, action=lambda: "ok"))

    def test_long_telegram_text_is_lossless_and_bounded(self):
        text = ("Раздел\n" + "строка " * 100 + "\n\n") * 20
        chunks = split_telegram_text(text, limit=200)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
        self.assertEqual(text, "".join(chunks))

    def test_recipient_keyboard_supports_multiple_selection(self):
        import logging
        controller = TelegramController(settings(), logging.getLogger("test"))
        keyboard = controller._recipient_keyboard(["a@test.ru", "b@test.ru"], {0, 1})
        labels = [row[0].text for row in keyboard.inline_keyboard]
        self.assertEqual(["✅ a@test.ru", "✅ b@test.ru"], labels)

    def test_smtp_errors(self):
        with self.assertRaisesRegex(DeliveryError, "Ошибка SMTP"):
            send_email_report(
                settings(), report_text="Текст", recipients=["a@example.test"],
                report_date="29.10.2026", smtp_factory=FailingSmtp,
            )


if __name__ == "__main__":
    unittest.main()
