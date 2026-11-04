"""Подтверждаемая однократная отправка текстового отчёта через SMTP SSL."""

from __future__ import annotations

import smtplib
import threading
from email.message import EmailMessage
from typing import Callable

from .config import Settings
from .recipients import normalize_recipients


class DeliveryError(RuntimeError):
    pass


class DuplicateDeliveryError(DeliveryError):
    pass


class ConfirmationRequiredError(DeliveryError):
    pass


class DeliveryGuard:
    """Защищает один процесс бота от повторного/параллельного подтверждения."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_or_sent: set[str] = set()

    def run_once(self, key: str, *, confirmed: bool, action: Callable[[], object]):
        if not confirmed:
            raise ConfirmationRequiredError("Отправка требует подтверждения")
        with self._lock:
            if key in self._active_or_sent:
                raise DuplicateDeliveryError("Этот отчёт уже отправляется или отправлен")
            self._active_or_sent.add(key)
        try:
            return action()
        except Exception:
            with self._lock:
                self._active_or_sent.discard(key)
            raise


def build_email_message(
    *, report_text: str, recipients: list[str], sender: str, report_date: str
) -> EmailMessage:
    normalized = normalize_recipients(recipients)
    if not normalized:
        raise DeliveryError("Не выбран ни один получатель")
    if not report_text.strip():
        raise DeliveryError("Текст отчёта пуст")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(normalized)
    message["Subject"] = f"Monitoring Bot V3 — отчёт {report_date}"
    message.set_content(report_text, charset="utf-8")
    return message


def send_email_report(
    settings: Settings,
    *,
    report_text: str,
    recipients: list[str],
    report_date: str,
    smtp_factory=smtplib.SMTP_SSL,
) -> tuple[str, ...]:
    settings.validate_email()
    normalized = tuple(normalize_recipients(recipients))
    message = build_email_message(
        report_text=report_text,
        recipients=list(normalized),
        sender=settings.email_login,
        report_date=report_date,
    )
    try:
        with smtp_factory(
            settings.email_smtp_host,
            settings.email_smtp_port,
            timeout=settings.email_timeout_seconds,
        ) as smtp:
            smtp.login(settings.email_login, settings.email_password)
            smtp.send_message(message)
    except Exception as exc:
        raise DeliveryError(f"Ошибка SMTP: {type(exc).__name__}") from exc
    return normalized
