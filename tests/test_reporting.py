from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from monitoring_bot_v3.matching import build_registry_index, match_news
from monitoring_bot_v3.models import (
    AnalysisData,
    CollateralRecord,
    MonitoringSourceRecord,
    NewsObject,
    TimeInterval,
)
from monitoring_bot_v3.reporting import (
    REPORT_COLUMNS,
    SECTION_EXACT,
    SECTION_INFORMATION,
    SECTION_QUARTER,
    build_report,
    render_html,
    render_json,
    render_text,
    save_report,
)


TZ = ZoneInfo("Asia/Yekaterinburg")
INTERVAL = TimeInterval(
    datetime(2026, 10, 22, tzinfo=TZ), datetime(2026, 10, 29, 9, 30, tzinfo=TZ)
)


def source(
    analysis_id,
    *,
    cad=None,
    address=None,
    locality="Екатеринбург",
    organization="Организация",
    region="Свердловская область",
    incident="пожар",
):
    obj = NewsObject(
        analysis_id=analysis_id,
        object_number="1",
        model=None,
        name=None,
        object_type=None,
        organization=organization,
        region=region,
        locality=locality,
        address=address,
        cadastral_number=cad,
        damage=None,
        evidence_fragment=None,
        ai_note=None,
        news_datetime=datetime(2026, 10, 25, 12, tzinfo=TZ),
        cadastral_quarter=None,
        all_cadastral_numbers=(),
        gemini=None,
    )
    return MonitoringSourceRecord(
        obj, AnalysisData(analysis_id, incident, f"https://example.test/{analysis_id}")
    )


def fixture_report():
    records = [
        CollateralRecord(
            "pledge-1", "Екатеринбург, улица Ленина, дом 1", "66:41:0303001:1", 2
        ),
        CollateralRecord(
            "quarter-1", "Екатеринбург, улица Мира, дом 2", "66:41:0303001:2", 3
        ),
    ]
    sources = [
        source(
            "exact-1",
            cad="66:41:0303001:1",
            address="улица Ленина, дом 1",
            incident="пожар",
            organization="АО <Опасность>",
        ),
        source(
            "exact-2",
            cad="66:41:0303001:1",
            address="улица Ленина, дом 1",
            incident="взрыв",
            organization="АО <Опасность>",
        ),
        source("not-pledged", cad="59:01:0000001:9", address=None),
        source(
            "no-data", cad=None, address=None, locality=None,
            organization=None, region=None, incident=None,
        ),
    ]
    result = match_news(build_registry_index(records), sources)
    return build_report(result, INTERVAL)


class ReportTests(unittest.TestCase):
    def test_sections_and_exact_column_contract(self):
        report = fixture_report()
        self.assertEqual(
            [SECTION_EXACT, SECTION_INFORMATION, SECTION_QUARTER],
            [section.title for section in report.sections],
        )
        for section in report.sections:
            for row in section.rows:
                self.assertEqual(REPORT_COLUMNS, tuple(row))
                self.assertEqual(9, len(row))

    def test_exact_row_uses_local_id_and_cadastral(self):
        row = fixture_report().sections[0].rows[0]
        self.assertEqual("pledge-1", row["ID из локальной таблицы"])
        self.assertEqual("66:41:0303001:1", row["Кадастровый номер"])
        self.assertEqual(
            "В залоге — кадастровый номер + адрес",
            row["В залоге / не в залоге (с признаком)"],
        )

    def test_incident_types_are_aggregated_without_duplicate_pledge(self):
        rows = fixture_report().sections[0].rows
        self.assertEqual(1, len(rows))
        self.assertEqual("взрыв; пожар", rows[0]["Тип происшествия"])

    def test_information_statuses_and_blank_local_id(self):
        rows = fixture_report().sections[1].rows
        self.assertEqual(
            {"Не в залоге", "Нет данных для идентификации"},
            {row["В залоге / не в залоге (с признаком)"] for row in rows},
        )
        self.assertTrue(all(not row["ID из локальной таблицы"] for row in rows))

    def test_quarter_is_not_pledged_and_not_duplicated(self):
        report = fixture_report()
        exact_ids = {r["ID из локальной таблицы"] for r in report.sections[0].rows}
        quarter = report.sections[2].rows
        self.assertEqual(1, len(quarter))
        self.assertNotIn(quarter[0]["ID из локальной таблицы"], exact_ids)
        self.assertEqual(
            "Не подтверждено — совпадение кадастрового квартала",
            quarter[0]["В залоге / не в залоге (с признаком)"],
        )

    def test_inn_is_blank_when_source_contract_has_no_inn(self):
        report = fixture_report()
        self.assertTrue(
            all(not row["ИНН"] for section in report.sections for row in section.rows)
        )

    def test_html_escapes_source_text_and_has_nine_headers_per_table(self):
        html = render_html(fixture_report())
        self.assertIn("АО &lt;Опасность&gt;", html)
        self.assertNotIn("АО <Опасность>", html)
        self.assertEqual(27, html.count("<th>"))

    def test_json_and_html_contain_same_agreed_values(self):
        report = fixture_report()
        payload = json.loads(render_json(report))
        json_row = payload["Разделы"][0]["Записи"][0]
        html = render_html(report)
        self.assertEqual("pledge-1", json_row["ID из локальной таблицы"])
        self.assertIn("pledge-1", html)
        self.assertEqual(REPORT_COLUMNS, tuple(json_row))

    def test_empty_sections_show_only_no_records_message(self):
        empty = match_news(build_registry_index([]), [])
        report = build_report(empty, INTERVAL)
        html = render_html(report)
        self.assertEqual(3, html.count("Нет записей"))
        self.assertNotIn("<table>", html)

    def test_save_report_never_overwrites_previous_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            first = save_report(fixture_report(), temp)
            second = save_report(fixture_report(), temp)
            self.assertNotEqual(first.html_path, second.html_path)
            self.assertNotEqual(first.json_path, second.json_path)
            self.assertTrue(first.html_path.is_file())
            self.assertTrue(second.json_path.is_file())
            self.assertTrue(first.txt_path.is_file())

    def test_text_report_has_exact_headings_date_and_only_agreed_fields(self):
        text = render_text(fixture_report())
        self.assertTrue(text.startswith("1. Происшествия с залоговым имуществом (22.10.2026 — 29.10.2026)"))
        self.assertLess(text.index("2.Основное в инфополе"), text.index("⚠️ В одном кадастровом квартале залоговое имущество!"))
        self.assertNotIn("Ссылка", text)
        self.assertNotIn("Описание", text)
        for column in REPORT_COLUMNS:
            self.assertNotIn(column + ":", text)
        first_entry = next(line for line in text.splitlines()[1:] if line)
        self.assertTrue(first_entry.startswith("- "))
        self.assertIn("; ", first_entry)
        self.assertNotIn("\nРегион:", text)

    def test_last_twelve_hours_can_use_only_end_date(self):
        report = build_report(
            match_news(build_registry_index([]), []),
            INTERVAL,
            use_end_date_only=True,
        )
        self.assertTrue(render_text(report).startswith(
            "1. Происшествия с залоговым имуществом (29.10.2026)"
        ))

    def test_empty_exact_text_uses_business_phrase(self):
        empty = build_report(match_news(build_registry_index([]), []), INTERVAL)
        text = render_text(empty)
        self.assertIn("Происшествий с залогом нет", text)
        self.assertEqual(1, text.count("Нет записей"))
        self.assertNotIn("⚠️ В одном кадастровом квартале", text)

    def test_non_empty_third_section_is_visible(self):
        text = render_text(fixture_report())
        self.assertIn("⚠️ В одном кадастровом квартале залоговое имущество!", text)
        lines = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(sum(len(section.rows) for section in fixture_report().sections), len(lines))


if __name__ == "__main__":
    unittest.main()
