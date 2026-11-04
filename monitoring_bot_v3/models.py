"""Внутренние модели источников и результата сопоставления."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class TimeInterval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Границы интервала должны содержать часовой пояс")
        if self.start >= self.end:
            raise ValueError("Начало периода должно быть раньше конца")

    def contains(self, value: datetime) -> bool:
        """Границы бизнес-интервала включительны."""
        if value.tzinfo is None:
            raise ValueError("Дата новости должна содержать часовой пояс")
        return self.start <= value <= self.end


@dataclass(frozen=True)
class AnalysisData:
    analysis_id: str
    incident_type: str | None
    source_url: str | None
    status: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class NewsObject:
    analysis_id: str
    object_number: str | None
    model: str | None
    name: str | None
    object_type: str | None
    organization: str | None
    region: str | None
    locality: str | None
    address: str | None
    cadastral_number: str | None
    damage: str | None
    evidence_fragment: str | None
    ai_note: str | None
    news_datetime: datetime
    cadastral_quarter: str | None
    all_cadastral_numbers: tuple[str, ...]
    gemini: str | None


@dataclass(frozen=True)
class MonitoringSourceRecord:
    object: NewsObject
    analysis: AnalysisData | None


@dataclass(frozen=True)
class CollateralRecord:
    object_id: str | None
    address: str | None
    cadastral_number: str | None
    source_row: int | None = None


@dataclass(frozen=True)
class CollateralScanResult:
    path: str
    sheet_name: str
    total_rows: int
    rows_with_id: int
    rows_with_address: int
    rows_with_cadastral: int
    elapsed_seconds: float


class PledgeStatus(StrEnum):
    PLEDGED = "В залоге"
    NOT_PLEDGED = "Не в залоге"
    NO_IDENTIFICATION_DATA = "Нет данных для идентификации"


class MatchReason(StrEnum):
    CADASTRAL = "Кадастровый номер"
    ADDRESS = "Адрес"
    CADASTRAL_AND_ADDRESS = "Кадастровый номер + адрес"


@dataclass(frozen=True)
class NewsMatchResult:
    source: MonitoringSourceRecord
    status: PledgeStatus
    cadastral_numbers: tuple[str, ...]
    normalized_address: str | None
    matched_collateral_keys: tuple[str, ...]
    cadastral_match_keys: tuple[str, ...]
    address_match_keys: tuple[str, ...]
    identifier_conflict: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExactCollateralMatch:
    collateral_key: str
    collateral: CollateralRecord
    reasons: tuple[MatchReason, ...]
    related_news: tuple[MonitoringSourceRecord, ...]
    incident_types: tuple[str, ...]
    source_urls: tuple[str, ...]
    has_identifier_conflict: bool


@dataclass(frozen=True)
class QuarterCollateralMatch:
    collateral_key: str
    collateral: CollateralRecord
    quarters: tuple[str, ...]
    related_news: tuple[MonitoringSourceRecord, ...]


@dataclass(frozen=True)
class MatchingDiagnostics:
    registry_rows: int
    indexed_registry_rows: int
    empty_registry_rows: int
    rows_without_id: int
    duplicate_id_values: int
    invalid_cadastral_rows: int
    unidentifiable_address_rows: int
    read_seconds: float
    index_seconds: float
    matching_seconds: float
    processed_news_objects: int
    exact_collateral_count: int
    quarter_collateral_count: int
    conflict_count: int
    address_cadastral_mismatch_count: int
    no_identification_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchingResult:
    exact_matches: tuple[ExactCollateralMatch, ...]
    information_field: tuple[NewsMatchResult, ...]
    quarter_matches: tuple[QuarterCollateralMatch, ...]
    per_news: tuple[NewsMatchResult, ...]
    diagnostics: MatchingDiagnostics

