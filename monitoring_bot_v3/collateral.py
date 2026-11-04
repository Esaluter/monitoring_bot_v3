"""Потоковое чтение локального реестра без изменения исходного Excel."""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .models import CollateralRecord, CollateralScanResult


class CollateralExcelError(RuntimeError):
    pass


_HEADER_ALIASES = {
    "address": {"адрес"},
    "cadastral": {"кадастровый", "кадастровый номер"},
    "id": {"id", "id залогового объекта", "ид"},
}


def _normalize_header(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def _column_indexes(headers: tuple[Any, ...]) -> dict[str, int]:
    actual = {
        _normalize_header(value): index
        for index, value in enumerate(headers)
        if value not in (None, "")
    }
    result: dict[str, int] = {}
    for field, aliases in _HEADER_ALIASES.items():
        found = next((actual[name] for name in aliases if name in actual), None)
        if found is None:
            raise CollateralExcelError(
                "В Excel отсутствует обязательная колонка: "
                + ", ".join(sorted(aliases))
            )
        result[field] = found
    return result


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return format(value, ".0f")
    text = str(value).strip()
    return text or None


def iter_collateral_records(path: Path | str) -> Iterator[CollateralRecord]:
    """Возвращает строки реестра; workbook всегда открыт только для чтения."""
    excel_path = Path(path)
    if not excel_path.is_file():
        raise FileNotFoundError(f"Файл залогового реестра не найден: {excel_path}")

    workbook = load_workbook(
        excel_path, read_only=True, data_only=True, keep_links=False
    )
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(values_only=True)
        try:
            headers = tuple(next(rows))
        except StopIteration as exc:
            raise CollateralExcelError("Файл залогового реестра пуст") from exc
        indexes = _column_indexes(headers)
        required_width = max(indexes.values()) + 1
        for source_row, row in enumerate(rows, start=2):
            values = tuple(row)
            if len(values) < required_width:
                values += (None,) * (required_width - len(values))
            yield CollateralRecord(
                object_id=_cell_text(values[indexes["id"]]),
                address=_cell_text(values[indexes["address"]]),
                cadastral_number=_cell_text(values[indexes["cadastral"]]),
                source_row=source_row,
            )
    finally:
        workbook.close()


def scan_collateral_excel(path: Path | str) -> CollateralScanResult:
    """Проверяет структуру и считает строки без накопления 758 тыс. записей."""
    excel_path = Path(path)
    started = time.perf_counter()
    total = with_id = with_address = with_cadastral = 0
    sheet_name = ""

    if not excel_path.is_file():
        raise FileNotFoundError(f"Файл залогового реестра не найден: {excel_path}")
    workbook = load_workbook(
        excel_path, read_only=True, data_only=True, keep_links=False
    )
    try:
        sheet_name = workbook.active.title
    finally:
        workbook.close()

    for record in iter_collateral_records(excel_path):
        total += 1
        with_id += record.object_id is not None
        with_address += record.address is not None
        with_cadastral += record.cadastral_number is not None

    return CollateralScanResult(
        path=str(excel_path.resolve()),
        sheet_name=sheet_name,
        total_rows=total,
        rows_with_id=with_id,
        rows_with_address=with_address,
        rows_with_cadastral=with_cadastral,
        elapsed_seconds=time.perf_counter() - started,
    )


def load_collateral_records(path: Path | str) -> list[CollateralRecord]:
    """Материализует реестр для будущего построения поисковых индексов."""
    return list(iter_collateral_records(path))

