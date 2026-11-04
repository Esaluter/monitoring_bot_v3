"""Чистые правила списка email; Google-транспорт находится в google_client."""

from __future__ import annotations

import re
from collections.abc import Iterable


_EMAIL_RE = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


class RecipientError(ValueError):
    pass


def normalize_email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if not _EMAIL_RE.fullmatch(email):
        raise RecipientError(f"Некорректный email: {value!r}")
    return email


def normalize_recipients(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        email = normalize_email(value)
        if email not in seen:
            result.append(email)
            seen.add(email)
    return result


def add_recipient(values: Iterable[str], value: str) -> list[str]:
    recipients = normalize_recipients(values)
    email = normalize_email(value)
    if email in recipients:
        raise RecipientError("Такой email уже есть в списке")
    return [*recipients, email]


def remove_recipient(values: Iterable[str], value: str) -> list[str]:
    recipients = normalize_recipients(values)
    email = normalize_email(value)
    if email not in recipients:
        raise RecipientError("Email не найден в списке")
    return [item for item in recipients if item != email]

