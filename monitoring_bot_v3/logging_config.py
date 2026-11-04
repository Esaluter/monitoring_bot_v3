"""Читаемое логирование с редактированием известных секретов."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable


_AUTH_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_BOT_URL_RE = re.compile(r"https://api\.telegram\.org/bot[^/\s]+", re.I)


class SecretRedactor:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self.secrets = tuple(
            sorted({value for value in secrets if value}, key=len, reverse=True)
        )

    def redact(self, value: object) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        text = _AUTH_RE.sub("[REDACTED]", text)
        return _BOT_URL_RE.sub(
            "https://api.telegram.org/bot[REDACTED]", text
        )


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: SecretRedactor) -> None:
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redactor.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: self.redactor.redact(value)
                    if isinstance(value, str)
                    else value
                    for key, value in record.args.items()
                }
            else:
                record.args = tuple(
                    self.redactor.redact(value)
                    if isinstance(value, str)
                    else value
                    for value in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, redactor: SecretRedactor) -> None:
        super().__init__(fmt)
        self.redactor = redactor

    def formatException(self, exc_info: object) -> str:
        return self.redactor.redact(super().formatException(exc_info))


def configure_logging(
    log_file: Path, *, secrets: Iterable[str] = ()
) -> logging.Logger:
    logger = logging.getLogger("monitoring_bot_v3")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    redactor = SecretRedactor(secrets)
    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(message)s", redactor
    )
    log_filter = RedactingFilter(redactor)

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.addFilter(log_filter)
    logger.addHandler(stream)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(log_filter)
    logger.addHandler(file_handler)

    for noisy_logger in ("requests", "urllib3", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    return logger

