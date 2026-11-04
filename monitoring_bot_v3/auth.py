"""Подготовленный перенос простой авторизации V2 без Telegram UI."""

from __future__ import annotations

import hmac


def verify_secret_word(provided: str | None, expected: str | None) -> bool:
    """Сравнивает секрет без логирования и без хранения состояния."""
    if not expected:
        raise RuntimeError("TELEGRAM_SECRET_WORD не настроен")
    provided_bytes = str(provided or "").encode("utf-8")
    expected_bytes = str(expected).encode("utf-8")
    return hmac.compare_digest(provided_bytes, expected_bytes)

