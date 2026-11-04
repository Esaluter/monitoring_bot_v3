"""Прикладная проверка источников без Telegram, отчёта и сопоставления."""

from __future__ import annotations

import logging
import time

from .collateral import scan_collateral_excel
from .config import Settings
from .google_client import AppsScriptClient
from .models import CollateralScanResult, MonitoringSourceRecord, TimeInterval
from .time_windows import format_interval


def make_google_client(
    settings: Settings, logger: logging.Logger
) -> AppsScriptClient:
    settings.validate_google()
    return AppsScriptClient(
        settings.google_apps_script_url,
        settings.google_apps_script_api_key,
        timeout_seconds=settings.google_http_timeout_seconds,
        attempts=settings.google_http_attempts,
        logger=logger,
        timezone_name=settings.timezone_name,
    )


def check_google_source(
    settings: Settings,
    interval: TimeInterval,
    logger: logging.Logger,
) -> list[MonitoringSourceRecord]:
    started = time.perf_counter()
    logger.info("Начало чтения Google Sheets: interval=%s", format_interval(interval))
    records = make_google_client(settings, logger).read_monitoring_data(interval)
    logger.info(
        "Чтение Google Sheets завершено: rows=%s elapsed=%.3fs",
        len(records),
        time.perf_counter() - started,
    )
    return records


def check_collateral_source(
    settings: Settings, logger: logging.Logger
) -> CollateralScanResult:
    logger.info("Начало проверки локального Excel")
    result = scan_collateral_excel(settings.collateral_excel_path)
    logger.info(
        "Проверка Excel завершена: rows=%s with_address=%s "
        "with_cadastral=%s elapsed=%.3fs",
        result.total_rows,
        result.rows_with_address,
        result.rows_with_cadastral,
        result.elapsed_seconds,
    )
    return result

