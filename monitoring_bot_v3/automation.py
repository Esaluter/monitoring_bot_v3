"""Локальное состояние и чистые правила автоматической рассылки."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def normalize_schedule_time(value: str) -> str:
    text = str(value or "").strip()
    match = _TIME_RE.fullmatch(text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError("Время должно быть в формате HH:MM от 00:00 до 23:59")
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def normalize_schedule(values) -> list[str]:
    return sorted({normalize_schedule_time(value) for value in values})


def next_scheduled_run(values, now: datetime) -> datetime | None:
    times = normalize_schedule(values)
    if not times:
        return None
    for day_offset in (0, 1):
        day = (now + timedelta(days=day_offset)).date()
        for value in times:
            hour, minute = map(int, value.split(":"))
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(
                hour=hour, minute=minute
            )
            if candidate > now:
                return candidate
    return None


def automation_enable_error(recipients, times) -> str | None:
    missing = []
    if not recipients:
        missing.append("нет получателей")
    if not times:
        missing.append("не настроено время отправки")
    return "; ".join(missing) if missing else None


@dataclass
class AutomationState:
    enabled: bool = False
    last_monitoring_at: str | None = None
    last_monitoring_result: str | None = None
    last_email_at: str | None = None
    last_email_result: str | None = None
    last_automatic_slot: str | None = None


class StateStore:
    """Потокобезопасное JSON-хранилище без секретов."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        self._state = self._load()

    def _load(self) -> AutomationState:
        if not self.path.exists():
            return AutomationState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            allowed = AutomationState.__dataclass_fields__
            return AutomationState(**{key: payload[key] for key in allowed if key in payload})
        except (OSError, ValueError, TypeError):
            return AutomationState()

    def get(self) -> AutomationState:
        with self._lock:
            return AutomationState(**asdict(self._state))

    def update(self, **values) -> AutomationState:
        with self._lock:
            for key, value in values.items():
                if key not in AutomationState.__dataclass_fields__:
                    raise ValueError(f"Неизвестное поле состояния: {key}")
                setattr(self._state, key, value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(asdict(self._state), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return AutomationState(**asdict(self._state))
