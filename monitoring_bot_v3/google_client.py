"""Защищённый HTTP-клиент отдельного Apps Script Monitoring Bot V3."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from .models import AnalysisData, MonitoringSourceRecord, NewsObject, TimeInterval
from .recipients import normalize_email, normalize_recipients
from .automation import normalize_schedule, normalize_schedule_time


class AppsScriptError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def parse_google_datetime(value: Any, tz: ZoneInfo) -> datetime:
    """Читает Date/ISO, отображаемую строку и Google serial date."""
    if isinstance(value, bool) or value is None or value == "":
        raise ValueError("Дата и время новости не указаны")
    if isinstance(value, datetime):
        return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)
    if isinstance(value, (int, float)):
        return datetime(1899, 12, 30, tzinfo=tz) + timedelta(days=float(value))

    text = str(value).strip()
    if not text:
        raise ValueError("Дата и время новости не указаны")
    try:
        numeric = float(text.replace(",", "."))
    except ValueError:
        numeric = None
    if numeric is not None:
        return datetime(1899, 12, 30, tzinfo=tz) + timedelta(days=numeric)

    iso_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)

    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(f"Некорректная дата и время новости: {text!r}")


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _cadastral_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        raw = [str(item) for item in value]
    else:
        raw = str(value or "").replace(";", "\n").splitlines()
    return tuple(dict.fromkeys(item.strip() for item in raw if item.strip()))


class AppsScriptClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        timeout_seconds: int = 30,
        attempts: int = 3,
        post: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
        timezone_name: str = "Asia/Yekaterinburg",
    ) -> None:
        if not url or not api_key:
            raise ValueError("Для Apps Script необходимы URL и API key")
        if timeout_seconds <= 0 or attempts <= 0:
            raise ValueError("Timeout и число попыток должны быть положительными")
        self.url = url
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.attempts = attempts
        self._post = post or requests.post
        self._sleep = sleep
        self.logger = logger or logging.getLogger("monitoring_bot_v3")
        self.tz = ZoneInfo(timezone_name)

    def _request(self, action: str, **parameters: Any) -> dict[str, Any]:
        body = {
            "version": 1,
            "action": action,
            "api_key": self._api_key,
            "request_id": str(uuid.uuid4()),
            **parameters,
        }
        last_error: AppsScriptError | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                response = self._post(
                    self.url,
                    json=body,
                    timeout=self.timeout_seconds,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                last_error = AppsScriptError(
                    f"Ошибка подключения к Apps Script: {type(exc).__name__}",
                    retryable=True,
                )
            else:
                status = int(getattr(response, "status_code", 0))
                if status == 429 or status >= 500:
                    last_error = AppsScriptError(
                        f"Временная HTTP-ошибка Apps Script: {status}",
                        retryable=True,
                    )
                elif status < 200 or status >= 300:
                    raise AppsScriptError(f"HTTP-ошибка Apps Script: {status}")
                else:
                    try:
                        payload = response.json()
                    except (TypeError, ValueError) as exc:
                        last_error = AppsScriptError(
                            "Apps Script вернул некорректный JSON", retryable=True
                        )
                    else:
                        if not isinstance(payload, dict):
                            last_error = AppsScriptError(
                                "Apps Script вернул некорректный ответ",
                                retryable=True,
                            )
                        elif not payload.get("ok"):
                            reason = str(payload.get("error") or "unknown_error")[:200]
                            retryable = reason in {"busy", "temporary_error", "internal_error"}
                            error = AppsScriptError(
                                f"Apps Script отклонил запрос: {reason}",
                                retryable=retryable,
                            )
                            if not retryable:
                                raise error
                            last_error = error
                        else:
                            return payload

            if attempt < self.attempts and last_error and last_error.retryable:
                self.logger.warning(
                    "Повтор запроса Apps Script: action=%s attempt=%s/%s",
                    action,
                    attempt + 1,
                    self.attempts,
                )
                self._sleep(min(2 ** (attempt - 1), 5))
                continue
            break
        raise last_error or AppsScriptError("Неизвестная ошибка Apps Script")

    def read_monitoring_data(
        self, interval: TimeInterval
    ) -> list[MonitoringSourceRecord]:
        payload = self._request(
            "read_monitoring_data",
            start=interval.start.isoformat(),
            end=interval.end.isoformat(),
        )
        rows = payload.get("records")
        if not isinstance(rows, list):
            raise AppsScriptError("В ответе Apps Script отсутствует список records")

        records: list[MonitoringSourceRecord] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise AppsScriptError(f"Некорректная запись records[{index}]")
            analysis_id = str(row.get("analysis_id") or "").strip()
            if not analysis_id:
                raise AppsScriptError(f"В records[{index}] отсутствует ID анализа")
            news_datetime = parse_google_datetime(row.get("news_datetime"), self.tz)
            # Защита от ошибочного/устаревшего серверного фильтра.
            if not interval.contains(news_datetime):
                continue

            analysis_value = row.get("analysis")
            analysis: AnalysisData | None = None
            if analysis_value is not None:
                if not isinstance(analysis_value, dict):
                    raise AppsScriptError(
                        f"Некорректный блок analysis в records[{index}]"
                    )
                linked_id = str(analysis_value.get("analysis_id") or analysis_id).strip()
                if linked_id != analysis_id:
                    raise AppsScriptError(
                        f"Несовпадающий ID анализа в records[{index}]"
                    )
                analysis = AnalysisData(
                    analysis_id=linked_id,
                    incident_type=_optional(analysis_value.get("incident_type")),
                    source_url=_optional(analysis_value.get("source_url")),
                    status=_optional(analysis_value.get("status")),
                    summary=_optional(analysis_value.get("summary")),
                )

            obj = NewsObject(
                analysis_id=analysis_id,
                object_number=_optional(row.get("object_number")),
                model=_optional(row.get("model")),
                name=_optional(row.get("name")),
                object_type=_optional(row.get("object_type")),
                organization=_optional(row.get("organization")),
                region=_optional(row.get("region")),
                locality=_optional(row.get("locality")),
                address=_optional(row.get("address")),
                cadastral_number=_optional(row.get("cadastral_number")),
                damage=_optional(row.get("damage")),
                evidence_fragment=_optional(row.get("evidence_fragment")),
                ai_note=_optional(row.get("ai_note")),
                news_datetime=news_datetime,
                cadastral_quarter=_optional(row.get("cadastral_quarter")),
                all_cadastral_numbers=_cadastral_values(
                    row.get("all_cadastral_numbers")
                ),
                gemini=_optional(row.get("gemini")),
            )
            records.append(MonitoringSourceRecord(object=obj, analysis=analysis))
        return records

    def read_recipients(self) -> list[str]:
        payload = self._request("read_recipients")
        values = payload.get("recipients")
        if not isinstance(values, list) or not all(
            isinstance(value, str) for value in values
        ):
            raise AppsScriptError("Некорректный список получателей")
        return normalize_recipients(values)

    def add_recipient(self, email: str) -> str:
        normalized = normalize_email(email)
        payload = self._request("add_recipient", email=normalized)
        status = payload.get("status")
        if status not in {"added", "exists"}:
            raise AppsScriptError("Некорректный статус добавления получателя")
        return str(status)

    def delete_recipient(self, email: str) -> str:
        normalized = normalize_email(email)
        payload = self._request("delete_recipient", email=normalized)
        status = payload.get("status")
        if status not in {"deleted", "not_found"}:
            raise AppsScriptError("Некорректный статус удаления получателя")
        return str(status)

    def read_schedule_times(self) -> list[str]:
        payload = self._request("read_schedule_times")
        values = payload.get("times")
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise AppsScriptError("Некорректный список времени рассылки")
        return normalize_schedule(values)

    def add_schedule_time(self, value: str) -> str:
        normalized = normalize_schedule_time(value)
        status = self._request("add_schedule_time", time=normalized).get("status")
        if status not in {"added", "exists"}:
            raise AppsScriptError("Некорректный статус добавления времени")
        return str(status)

    def delete_schedule_time(self, value: str) -> str:
        normalized = normalize_schedule_time(value)
        status = self._request("delete_schedule_time", time=normalized).get("status")
        if status not in {"deleted", "not_found"}:
            raise AppsScriptError("Некорректный статус удаления времени")
        return str(status)

