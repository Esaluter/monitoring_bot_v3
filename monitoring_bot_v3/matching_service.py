"""Оркестрация чтения, индексации и сопоставления без интерфейсной логики."""

from __future__ import annotations

import logging
import time

from .collateral import iter_collateral_records
from .config import Settings
from .matching import build_registry_index, match_news
from .models import MatchingResult, TimeInterval
from .source_service import check_google_source


def run_matching(
    settings: Settings, interval: TimeInterval, logger: logging.Logger
) -> MatchingResult:
    sources = check_google_source(settings, interval, logger)
    started = time.perf_counter()
    records = []
    source_rows = empty_rows = 0
    for record in iter_collateral_records(settings.collateral_excel_path):
        source_rows += 1
        if not (record.object_id or record.address or record.cadastral_number):
            empty_rows += 1
            continue
        records.append(record)
    read_seconds = time.perf_counter() - started
    logger.info(
        "Реестр прочитан: source_rows=%s indexable_rows=%s empty_rows=%s elapsed=%.3fs",
        source_rows, len(records), empty_rows, read_seconds,
    )
    index = build_registry_index(
        records, source_rows=source_rows, skipped_empty_rows=empty_rows
    )
    logger.info(
        "Индексы построены: cadastral=%s address=%s quarter=%s elapsed=%.3fs",
        len(index.by_cadastral), len(index.by_address), len(index.by_quarter),
        index.build_seconds,
    )
    result = match_news(index, sources, read_seconds=read_seconds)
    logger.info(
        "Сопоставление завершено: news=%s exact=%s quarter=%s conflicts=%s elapsed=%.3fs",
        result.diagnostics.processed_news_objects,
        result.diagnostics.exact_collateral_count,
        result.diagnostics.quarter_collateral_count,
        result.diagnostics.conflict_count,
        result.diagnostics.matching_seconds,
    )
    return result
