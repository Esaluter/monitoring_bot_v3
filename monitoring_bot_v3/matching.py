"""Индексируемый движок точного и квартального сопоставления."""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .models import (
    CollateralRecord,
    ExactCollateralMatch,
    MatchReason,
    MatchingDiagnostics,
    MatchingResult,
    MonitoringSourceRecord,
    NewsMatchResult,
    PledgeStatus,
    QuarterCollateralMatch,
)
from .normalization import (
    cadastral_quarter,
    extract_cadastral_numbers,
    normalize_address,
)


IndexValue = int | list[int]


def _add(index: dict[str, IndexValue], key: str, row_index: int) -> None:
    current = index.get(key)
    if current is None:
        index[key] = row_index
    elif isinstance(current, int):
        index[key] = [current, row_index]
    else:
        current.append(row_index)


def _values(index: dict[str, IndexValue], key: str) -> tuple[int, ...]:
    value = index.get(key)
    if value is None:
        return ()
    return (value,) if isinstance(value, int) else tuple(value)


@dataclass(frozen=True)
class RegistryIndex:
    records: tuple[CollateralRecord, ...]
    by_cadastral: dict[str, IndexValue]
    by_address: dict[str, IndexValue]
    by_quarter: dict[str, IndexValue]
    id_counts: dict[str, int]
    missing_ids: int
    duplicate_id_values: int
    invalid_cadastral_rows: int
    unidentifiable_address_rows: int
    build_seconds: float
    source_rows: int
    skipped_empty_rows: int

    def record_key(self, row_index: int) -> str:
        record = self.records[row_index]
        if record.object_id and self.id_counts.get(record.object_id) == 1:
            return f"id:{record.object_id}"
        source_row = record.source_row if record.source_row is not None else row_index + 2
        return f"row:{source_row}"

    def lookup_cadastral(self, value: str) -> tuple[int, ...]:
        return _values(self.by_cadastral, value)

    def lookup_address(self, value: str) -> tuple[int, ...]:
        return _values(self.by_address, value)

    def lookup_quarter(self, value: str) -> tuple[int, ...]:
        return _values(self.by_quarter, value)


def build_registry_index(
    records: Iterable[CollateralRecord],
    *,
    source_rows: int | None = None,
    skipped_empty_rows: int = 0,
) -> RegistryIndex:
    started = time.perf_counter()
    materialized = tuple(records)
    id_counts = Counter(r.object_id for r in materialized if r.object_id)
    by_cadastral: dict[str, IndexValue] = {}
    by_address: dict[str, IndexValue] = {}
    by_quarter: dict[str, IndexValue] = {}
    invalid_cadastral = unidentifiable_address = 0

    for row_index, record in enumerate(materialized):
        numbers = extract_cadastral_numbers(record.cadastral_number)
        if record.cadastral_number and not numbers:
            invalid_cadastral += 1
        for number in numbers:
            _add(by_cadastral, number, row_index)
            quarter = cadastral_quarter(number)
            if quarter:
                _add(by_quarter, quarter, row_index)

        address = normalize_address(record.address)
        if address and address.is_identifiable:
            _add(by_address, address.key, row_index)
        elif record.address:
            unidentifiable_address += 1

    return RegistryIndex(
        records=materialized,
        by_cadastral=by_cadastral,
        by_address=by_address,
        by_quarter=by_quarter,
        id_counts=dict(id_counts),
        missing_ids=sum(r.object_id is None for r in materialized),
        duplicate_id_values=sum(count > 1 for count in id_counts.values()),
        invalid_cadastral_rows=invalid_cadastral,
        unidentifiable_address_rows=unidentifiable_address,
        build_seconds=time.perf_counter() - started,
        source_rows=source_rows if source_rows is not None else len(materialized),
        skipped_empty_rows=skipped_empty_rows,
    )


def _source_key(source: MonitoringSourceRecord) -> tuple[str, ...]:
    obj = source.object
    return (
        obj.analysis_id,
        obj.object_number or "",
        obj.news_datetime.isoformat(),
        obj.address or "",
        obj.cadastral_number or "",
    )


def _news_cadastral(source: MonitoringSourceRecord) -> tuple[str, ...]:
    values = [source.object.cadastral_number, *source.object.all_cadastral_numbers]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for number in extract_cadastral_numbers(value):
            if number not in seen:
                seen.add(number)
                result.append(number)
    return tuple(result)


def match_news(
    index: RegistryIndex,
    sources: Iterable[MonitoringSourceRecord],
    *,
    read_seconds: float = 0.0,
) -> MatchingResult:
    started = time.perf_counter()
    ordered_sources = sorted(sources, key=_source_key)
    per_news: list[NewsMatchResult] = []
    exact_data: dict[str, dict[str, object]] = {}
    quarter_data: dict[str, dict[str, object]] = {}

    for source in ordered_sources:
        numbers = _news_cadastral(source)
        address = normalize_address(
            source.object.address, locality=source.object.locality
        )
        cad_rows = {i for number in numbers for i in index.lookup_cadastral(number)}
        address_rows = (
            set(index.lookup_address(address.key))
            if address and address.is_identifiable
            else set()
        )
        conflict = bool(cad_rows and address_rows and cad_rows != address_rows)
        match_rows = cad_rows | address_rows
        warnings: list[str] = []
        if source.object.cadastral_number and not numbers:
            warnings.append("Некорректный полный кадастровый номер")
        if address and not address.is_identifiable:
            warnings.append(f"Недостаточно конкретный адрес: {address.reason}")
        if conflict:
            warnings.append("Конфликт кадастрового номера и адреса")

        address_cadastral_mismatches: list[str] = []
        for row_index in sorted(address_rows, key=index.record_key):
            collateral_numbers = set(
                extract_cadastral_numbers(index.records[row_index].cadastral_number)
            )
            if numbers and collateral_numbers and not (set(numbers) & collateral_numbers):
                address_cadastral_mismatches.append(index.record_key(row_index))
        for key in address_cadastral_mismatches:
            warnings.append(
                "Адрес совпал, но полный кадастровый номер залога отличается: " + key
            )

        matched_keys: list[str] = []
        for row_index in sorted(match_rows, key=index.record_key):
            key = index.record_key(row_index)
            matched_keys.append(key)
            in_cad, in_address = row_index in cad_rows, row_index in address_rows
            reason = (
                MatchReason.CADASTRAL_AND_ADDRESS
                if in_cad and in_address
                else MatchReason.CADASTRAL
                if in_cad
                else MatchReason.ADDRESS
            )
            data = exact_data.setdefault(
                key,
                {"row": row_index, "reasons": set(), "news": {}, "conflict": False},
            )
            data["reasons"].add(reason)  # type: ignore[union-attr]
            data["news"][_source_key(source)] = source  # type: ignore[index]
            data["conflict"] = bool(data["conflict"]) or conflict

        has_identifiers = bool(numbers or (address and address.is_identifiable))
        status = (
            PledgeStatus.PLEDGED
            if match_rows
            else PledgeStatus.NOT_PLEDGED
            if has_identifiers
            else PledgeStatus.NO_IDENTIFICATION_DATA
        )
        news_result = NewsMatchResult(
            source=source,
            status=status,
            cadastral_numbers=numbers,
            normalized_address=address.key if address else None,
            matched_collateral_keys=tuple(matched_keys),
            cadastral_match_keys=tuple(
                sorted((index.record_key(i) for i in cad_rows))
            ),
            address_match_keys=tuple(
                sorted((index.record_key(i) for i in address_rows))
            ),
            identifier_conflict=conflict,
            warnings=tuple(warnings),
        )
        per_news.append(news_result)

        for quarter in sorted({cadastral_quarter(n) for n in numbers} - {None}):
            for row_index in index.lookup_quarter(quarter):
                if row_index in match_rows:
                    continue
                key = index.record_key(row_index)
                data = quarter_data.setdefault(
                    key, {"row": row_index, "quarters": set(), "news": {}}
                )
                data["quarters"].add(quarter)  # type: ignore[union-attr]
                data["news"][_source_key(source)] = source  # type: ignore[index]

    exact_keys = set(exact_data)
    exact_matches: list[ExactCollateralMatch] = []
    for key in sorted(exact_data):
        data = exact_data[key]
        related = tuple(data["news"][k] for k in sorted(data["news"]))  # type: ignore[index]
        incident_types = tuple(
            sorted({s.analysis.incident_type for s in related if s.analysis and s.analysis.incident_type})
        )
        urls = tuple(
            sorted({s.analysis.source_url for s in related if s.analysis and s.analysis.source_url})
        )
        exact_matches.append(
            ExactCollateralMatch(
                key,
                index.records[data["row"]],  # type: ignore[index]
                tuple(sorted(data["reasons"], key=str)),  # type: ignore[arg-type]
                related,
                incident_types,
                urls,
                bool(data["conflict"]),
            )
        )

    quarter_matches: list[QuarterCollateralMatch] = []
    for key in sorted(set(quarter_data) - exact_keys):
        data = quarter_data[key]
        quarter_matches.append(
            QuarterCollateralMatch(
                key,
                index.records[data["row"]],  # type: ignore[index]
                tuple(sorted(data["quarters"])),  # type: ignore[arg-type]
                tuple(data["news"][k] for k in sorted(data["news"])),  # type: ignore[index]
            )
        )

    information = tuple(r for r in per_news if r.status != PledgeStatus.PLEDGED)
    warnings: list[str] = []
    if index.missing_ids:
        warnings.append(f"Строк без ID: {index.missing_ids}; использован ключ строки")
    if index.duplicate_id_values:
        warnings.append(
            f"Неуникальных значений ID: {index.duplicate_id_values}; записи не объединялись"
        )
    if index.skipped_empty_rows:
        warnings.append(
            f"Полностью пустых строк реестра пропущено: {index.skipped_empty_rows}"
        )
    diagnostics = MatchingDiagnostics(
        registry_rows=index.source_rows,
        indexed_registry_rows=len(index.records),
        empty_registry_rows=index.skipped_empty_rows,
        rows_without_id=index.missing_ids,
        duplicate_id_values=index.duplicate_id_values,
        invalid_cadastral_rows=index.invalid_cadastral_rows,
        unidentifiable_address_rows=index.unidentifiable_address_rows,
        read_seconds=read_seconds,
        index_seconds=index.build_seconds,
        matching_seconds=time.perf_counter() - started,
        processed_news_objects=len(per_news),
        exact_collateral_count=len(exact_matches),
        quarter_collateral_count=len(quarter_matches),
        conflict_count=sum(r.identifier_conflict for r in per_news),
        address_cadastral_mismatch_count=sum(
            any(w.startswith("Адрес совпал, но полный кадастровый номер") for w in r.warnings)
            for r in per_news
        ),
        no_identification_count=sum(
            r.status == PledgeStatus.NO_IDENTIFICATION_DATA for r in per_news
        ),
        warnings=tuple(warnings),
    )
    return MatchingResult(
        tuple(exact_matches), information, tuple(quarter_matches), tuple(per_news), diagnostics
    )
