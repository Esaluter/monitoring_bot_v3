"""Конфигурация проекта из окружения и локального .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .time_windows import timezone


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    pass


def _positive_int(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть целым числом") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} должен быть больше нуля")
    return value


def _project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_secret_word: str
    google_apps_script_url: str
    google_apps_script_api_key: str
    google_http_timeout_seconds: int
    google_http_attempts: int
    collateral_excel_path: Path
    lookback_hours: int
    timezone_name: str
    log_file: Path
    email_smtp_host: str
    email_smtp_port: int
    email_login: str
    email_password: str
    email_timeout_seconds: int
    telegram_monitoring_channel_id: str = ""
    automation_state_file: Path = PROJECT_ROOT / "data" / "automation_state.json"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_dotenv(dotenv_path=env_file or PROJECT_ROOT / ".env", override=False)
        timezone_name = os.getenv("TIMEZONE", "Asia/Yekaterinburg").strip()
        try:
            timezone(timezone_name)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc

        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            # Секрет сравнивается буквально: регистр и окружающие пробелы значимы.
            telegram_secret_word=os.getenv("TELEGRAM_SECRET_WORD", ""),
            google_apps_script_url=os.getenv("GOOGLE_APPS_SCRIPT_URL", "").strip(),
            google_apps_script_api_key=os.getenv(
                "GOOGLE_APPS_SCRIPT_API_KEY", ""
            ).strip(),
            google_http_timeout_seconds=_positive_int(
                "GOOGLE_HTTP_TIMEOUT_SECONDS", "30"
            ),
            google_http_attempts=_positive_int("GOOGLE_HTTP_ATTEMPTS", "3"),
            collateral_excel_path=_project_path(
                os.getenv("COLLATERAL_EXCEL_PATH", "ID_Недвижимость v.2.xlsx").strip()
            ),
            lookback_hours=_positive_int("LOOKBACK_HOURS", "12"),
            timezone_name=timezone_name,
            log_file=_project_path(
                os.getenv("LOG_FILE", "logs/monitoring_bot_v3.log").strip()
            ),
            email_smtp_host=os.getenv("EMAIL_SMTP_HOST", "smtp.yandex.ru").strip(),
            email_smtp_port=_positive_int("EMAIL_SMTP_PORT", "465"),
            email_login=os.getenv("EMAIL_LOGIN", "").strip(),
            email_password=os.getenv("EMAIL_PASSWORD", "").strip(),
            email_timeout_seconds=_positive_int("EMAIL_TIMEOUT_SECONDS", "30"),
            telegram_monitoring_channel_id=os.getenv(
                "TELEGRAM_MONITORING_CHANNEL_ID", ""
            ).strip(),
            automation_state_file=_project_path(
                os.getenv(
                    "AUTOMATION_STATE_FILE", "data/automation_state.json"
                ).strip()
            ),
        )

    def validate_google(self) -> None:
        if not self.google_apps_script_url:
            raise ConfigurationError("Не задан GOOGLE_APPS_SCRIPT_URL")
        parsed = urlparse(self.google_apps_script_url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "script.google.com"
            or not parsed.path.endswith("/exec")
        ):
            raise ConfigurationError(
                "GOOGLE_APPS_SCRIPT_URL должен быть HTTPS URL script.google.com, "
                "заканчивающимся на /exec"
            )
        if not self.google_apps_script_api_key:
            raise ConfigurationError("Не задан GOOGLE_APPS_SCRIPT_API_KEY")

    def validate_telegram(self) -> None:
        if not self.telegram_bot_token:
            raise ConfigurationError("Не задан TELEGRAM_BOT_TOKEN")
        if not self.telegram_secret_word:
            raise ConfigurationError("Не задан TELEGRAM_SECRET_WORD")

    def validate_email(self) -> None:
        if not self.email_smtp_host:
            raise ConfigurationError("Не задан EMAIL_SMTP_HOST")
        if not self.email_login:
            raise ConfigurationError("Не задан EMAIL_LOGIN")
        if not self.email_password:
            raise ConfigurationError("Не задан EMAIL_PASSWORD")

