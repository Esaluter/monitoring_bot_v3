"""Строгое представление отчёта этапа 3 в HTML и JSON."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from .models import MatchReason, MatchingResult, MonitoringSourceRecord, TimeInterval
from .normalization import normalize_address
from .time_windows import format_interval


REPORT_COLUMNS = (
    "Организация",
    "Регион",
    "Город",
    "Улица",
    "ИНН",
    "Кадастровый номер",
    "ID из локальной таблицы",
    "Тип происшествия",
    "В залоге / не в залоге (с признаком)",
)

SECTION_EXACT = "Происшествия с залоговыми объектами"
SECTION_INFORMATION = "Основное в инфополе"
SECTION_QUARTER = "Залоговые объекты в том же кадастровом квартале"


@dataclass(frozen=True)
class ReportSection:
    title: str
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class ReportDocument:
    title: str
    period: str
    report_date: str
    sections: tuple[ReportSection, ...]


@dataclass(frozen=True)
class SavedReport:
    html_path: Path
    json_path: Path
    txt_path: Path


def _value(value: object | None) -> str:
    return "" if value is None else str(value).strip()


def _joined(values) -> str:
    return "; ".join(sorted({_value(value) for value in values if _value(value)}))


def _row(**values: str) -> dict[str, str]:
    row = {column: "" for column in REPORT_COLUMNS}
    row.update(values)
    if tuple(row) != REPORT_COLUMNS:
        raise ValueError("Строка отчёта содержит несогласованные поля")
    return row


def _news_values(sources: tuple[MonitoringSourceRecord, ...]) -> dict[str, str]:
    return {
        "Организация": _joined(s.object.organization for s in sources),
        "Регион": _joined(s.object.region for s in sources),
        "Город": _joined(s.object.locality for s in sources),
        # В контракте Google это поле называется «Адрес / ориентир». Отдельного
        # поля улицы нет, поэтому сохраняется исходное значение без выдумывания.
        "Улица": _joined(s.object.address for s in sources),
        "Тип происшествия": _joined(
            s.analysis.incident_type for s in sources if s.analysis
        ),
    }


def _exact_status(reasons: tuple[MatchReason, ...]) -> str:
    reason_set = set(reasons)
    if (
        MatchReason.CADASTRAL_AND_ADDRESS in reason_set
        or {MatchReason.CADASTRAL, MatchReason.ADDRESS} <= reason_set
    ):
        return "В залоге — кадастровый номер + адрес"
    if MatchReason.CADASTRAL in reason_set:
        return "В залоге — кадастровый номер"
    if MatchReason.ADDRESS in reason_set:
        return "В залоге — адрес"
    raise ValueError("У точного совпадения отсутствует основание")


def _locality_from_collateral_address(address: str | None) -> str:
    normalized = normalize_address(address)
    if not normalized or not normalized.is_identifiable:
        return ""
    # В локальном контракте нет отдельной колонки города. Возвращаем только
    # фактический первый компонент исходного адреса, не реконструируя значение.
    if address and "," in address:
        return address.split(",", 1)[0].strip()
    return ""


def build_report(
    result: MatchingResult,
    interval: TimeInterval,
    *,
    use_end_date_only: bool = False,
) -> ReportDocument:
    exact_rows: list[dict[str, str]] = []
    for match in result.exact_matches:
        news = _news_values(match.related_news)
        exact_rows.append(
            _row(
                **news,
                **{
                    "ИНН": "",  # В текущем контракте источников поля ИНН нет.
                    "Кадастровый номер": _value(match.collateral.cadastral_number),
                    "ID из локальной таблицы": _value(match.collateral.object_id),
                    "В залоге / не в залоге (с признаком)": _exact_status(
                        match.reasons
                    ),
                },
            )
        )

    information_rows: list[dict[str, str]] = []
    for item in result.information_field:
        source = item.source
        cadastral = source.object.cadastral_number or _joined(
            source.object.all_cadastral_numbers
        )
        information_rows.append(
            _row(
                **{
                    "Организация": _value(source.object.organization),
                    "Регион": _value(source.object.region),
                    "Город": _value(source.object.locality),
                    "Улица": _value(source.object.address),
                    "ИНН": "",
                    "Кадастровый номер": _value(cadastral),
                    "ID из локальной таблицы": "",
                    "Тип происшествия": _value(
                        source.analysis.incident_type if source.analysis else None
                    ),
                    "В залоге / не в залоге (с признаком)": str(item.status),
                }
            )
        )

    quarter_rows: list[dict[str, str]] = []
    exact_keys = {match.collateral_key for match in result.exact_matches}
    for match in result.quarter_matches:
        if match.collateral_key in exact_keys:
            continue
        quarter_rows.append(
            _row(
                **{
                    "Организация": "",
                    "Регион": "",
                    "Город": _locality_from_collateral_address(
                        match.collateral.address
                    ),
                    "Улица": _value(match.collateral.address),
                    "ИНН": "",
                    "Кадастровый номер": _value(
                        match.collateral.cadastral_number
                    ),
                    "ID из локальной таблицы": _value(match.collateral.object_id),
                    "Тип происшествия": _joined(
                        source.analysis.incident_type
                        for source in match.related_news
                        if source.analysis
                    ),
                    "В залоге / не в залоге (с признаком)": (
                        "Не подтверждено — совпадение кадастрового квартала"
                    ),
                }
            )
        )

    start_date = interval.start.strftime("%d.%m.%Y")
    end_date = interval.end.strftime("%d.%m.%Y")
    report_date = end_date if use_end_date_only or start_date == end_date else f"{start_date} — {end_date}"
    return ReportDocument(
        title="Отчёт Monitoring Bot V3",
        period=format_interval(interval),
        report_date=report_date,
        sections=(
            ReportSection(SECTION_EXACT, tuple(exact_rows)),
            ReportSection(SECTION_INFORMATION, tuple(information_rows)),
            ReportSection(SECTION_QUARTER, tuple(quarter_rows)),
        ),
    )


def report_as_dict(report: ReportDocument) -> dict[str, object]:
    return {
        "Название": report.title,
        "Отчётный период": report.period,
        "Разделы": [
            {"Название": section.title, "Записи": list(section.rows)}
            for section in report.sections
        ],
    }


def render_json(report: ReportDocument) -> str:
    return json.dumps(report_as_dict(report), ensure_ascii=False, indent=2)


def render_text(report: ReportDocument) -> str:
    headings = (
        f"1. Происшествия с залоговым имуществом ({report.report_date})",
        "2.Основное в инфополе",
        "⚠️ В одном кадастровом квартале залоговое имущество!",
    )
    parts: list[str] = []
    for section_index, (heading, section) in enumerate(zip(headings, report.sections)):
        if section_index == 2 and not section.rows:
            continue
        parts.append(heading)
        if not section.rows:
            parts.append(
                "Происшествий с залогом нет" if section_index == 0 else "Нет записей"
            )
            continue
        entries: list[str] = []
        for row in section.rows:
            values = [
                row[column].replace("\r", " ").replace("\n", " ").strip()
                for column in REPORT_COLUMNS
                if row[column].strip()
            ]
            entries.append("- " + "; ".join(values))
        parts.append("\n".join(entries))
    return "\n\n".join(parts) + "\n"


def render_html(report: ReportDocument) -> str:
    parts = [
        "<!doctype html>",
        '<html lang="ru"><head><meta charset="utf-8">',
        f"<title>{escape(report.title)}</title>",
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#222}"
        "table{border-collapse:collapse;width:100%;margin-bottom:28px}"
        "th,td{border:1px solid #bbb;padding:7px;text-align:left;vertical-align:top}"
        "th{background:#eee}h2{margin-top:28px}</style></head><body>",
        f"<h1>{escape(report.title)}</h1>",
        f"<p>Отчётный период: {escape(report.period)}</p>",
    ]
    for section in report.sections:
        parts.append(f"<h2>{escape(section.title)}</h2>")
        if not section.rows:
            parts.append("<p>Нет записей</p>")
            continue
        parts.append("<table><thead><tr>")
        parts.extend(f"<th>{escape(column)}</th>" for column in REPORT_COLUMNS)
        parts.append("</tr></thead><tbody>")
        for row in section.rows:
            parts.append("<tr>")
            parts.extend(f"<td>{escape(row[column])}</td>" for column in REPORT_COLUMNS)
            parts.append("</tr>")
        parts.append("</tbody></table>")
    parts.append("</body></html>")
    return "\n".join(parts)


def save_report(report: ReportDocument, reports_dir: Path | str) -> SavedReport:
    directory = Path(reports_dir)
    directory.mkdir(parents=True, exist_ok=True)
    moment = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"monitoring_report_{moment}_{uuid.uuid4().hex[:8]}"
    html_path = directory / f"{stem}.html"
    json_path = directory / f"{stem}.json"
    txt_path = directory / f"{stem}.txt"
    with html_path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(render_html(report))
    with json_path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(render_json(report))
    with txt_path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(render_text(report))
    return SavedReport(html_path.resolve(), json_path.resolve(), txt_path.resolve())
