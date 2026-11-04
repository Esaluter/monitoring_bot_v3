"""Разбор интервалов Monitoring Bot V3 в часовом поясе Екатеринбурга."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import TimeInterval


class IntervalParseError(ValueError):
    """Ошибка пользовательского формата или границ интервала."""


INTERVAL_EXAMPLES = (
    "10.10.2026 15:00 - 11.10.2026 16:00",
    "10.10.2026 1500 - 11.10.2026 1600",
    "10102026 1500 11102026 1600",
    "101020261500-111020261600",
    "10.10.26 15:00 - 11.10.26 16:00",
    "10.10.2026 - 11.10.2026",
    "10102026-11102026",
)

_DATETIME_PATTERNS = (
    re.compile(r"^\s*(\d{2}\.\d{2}\.(?:\d{2}|\d{4}))\s+(\d{2}:?\d{2})\s*(?:-\s*)?(\d{2}\.\d{2}\.(?:\d{2}|\d{4}))\s+(\d{2}:?\d{2})\s*$"),
    re.compile(r"^\s*(\d{8})\s+(\d{4})\s+(\d{8})\s+(\d{4})\s*$"),
    re.compile(r"^\s*(\d{8})(\d{4})\s*-\s*(\d{8})(\d{4})\s*$"),
)
_DATE_ONLY_PATTERNS = (
    re.compile(r"^\s*(\d{2}\.\d{2}\.(?:\d{2}|\d{4}))\s*-\s*(\d{2}\.\d{2}\.(?:\d{2}|\d{4}))\s*$"),
    re.compile(r"^\s*(\d{8})\s*-\s*(\d{8})\s*$"),
)


def timezone(name: str = "Asia/Yekaterinburg") -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise IntervalParseError(f"Неизвестный часовой пояс: {name}") from exc


def _parse_datetime(date_value: str, time_value: str, tz: ZoneInfo) -> datetime:
    if "." not in date_value:
        date_value = f"{date_value[:2]}.{date_value[2:4]}.{date_value[4:]}"
    date_format = "%d.%m.%Y" if len(date_value.split(".")[-1]) == 4 else "%d.%m.%y"
    time_format = "%H:%M" if ":" in time_value else "%H%M"
    try:
        parsed = datetime.strptime(f"{date_value} {time_value}", f"{date_format} {time_format}")
    except ValueError as exc:
        raise IntervalParseError(
            f"Некорректная дата или время: {date_value} {time_value}"
        ) from exc
    return parsed.replace(tzinfo=tz)


def parse_exact_interval(
    value: str, timezone_name: str = "Asia/Yekaterinburg"
) -> TimeInterval:
    """Разбирает согласованные форматы; даты без времени означают полные сутки."""
    text = str(value or "").strip()
    if not text:
        raise IntervalParseError("Интервал не указан")

    tz = timezone(timezone_name)
    for pattern in _DATETIME_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            start = _parse_datetime(match.group(1), match.group(2), tz)
            end = _parse_datetime(match.group(3), match.group(4), tz)
            break
    else:
        for pattern in _DATE_ONLY_PATTERNS:
            match = pattern.fullmatch(text)
            if match:
                start = _parse_datetime(match.group(1), "00:00", tz)
                # Inclusive final calendar day represented by its last microsecond.
                end = _parse_datetime(match.group(2), "00:00", tz) + timedelta(days=1) - timedelta(microseconds=1)
                break
        else:
            raise IntervalParseError(
                "Не удалось распознать интервал. Примеры допустимого ввода:\n"
                + "\n".join(INTERVAL_EXAMPLES)
            )

    if start >= end:
        raise IntervalParseError("Начало периода должно быть раньше конца")
    return TimeInterval(start=start, end=end)


def lookback_interval(
    hours: int = 12,
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Yekaterinburg",
) -> TimeInterval:
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
        raise IntervalParseError("Количество часов должно быть положительным целым числом")
    tz = timezone(timezone_name)
    end = now or datetime.now(tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    else:
        end = end.astimezone(tz)
    return TimeInterval(start=end - timedelta(hours=hours), end=end)


def format_interval(interval: TimeInterval) -> str:
    return (
        f"{interval.start:%d.%m.%Y %H:%M:%S %Z} — "
        f"{interval.end:%d.%m.%Y %H:%M:%S %Z}"
    )

