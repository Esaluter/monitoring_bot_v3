"""Единый сценарий формирования всех форматов одного отчёта."""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PROJECT_ROOT, Settings
from .matching_service import run_matching
from .models import MatchingResult, TimeInterval
from .reporting import ReportDocument, SavedReport, build_report, save_report


def generate_report_bundle(
    settings: Settings,
    interval: TimeInterval,
    logger: logging.Logger,
    *,
    reports_dir: Path | None = None,
    use_end_date_only: bool = False,
) -> tuple[MatchingResult, ReportDocument, SavedReport]:
    matching = run_matching(settings, interval, logger)
    report = build_report(matching, interval, use_end_date_only=use_end_date_only)
    saved = save_report(report, reports_dir or PROJECT_ROOT / "reports")
    logger.info(
        "Отчёт сохранён: html=%s json=%s txt=%s",
        saved.html_path.name, saved.json_path.name, saved.txt_path.name,
    )
    return matching, report, saved
