from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from monitoring_bot_v3.matching import build_registry_index, match_news
from monitoring_bot_v3.models import (
    AnalysisData,
    CollateralRecord,
    MatchReason,
    MonitoringSourceRecord,
    NewsObject,
    PledgeStatus,
)
from monitoring_bot_v3.normalization import (
    cadastral_quarter,
    extract_cadastral_numbers,
    normalize_address,
    normalize_cadastral_number,
)


TZ = ZoneInfo("Asia/Yekaterinburg")


def collateral(object_id, cad=None, address=None, row=2):
    return CollateralRecord(object_id, address, cad, row)


def news(
    *, cad=None, address=None, all_cads=(), analysis_id="a1", number="1",
    incident="пожар", url="https://example.test/1", locality=None,
):
    obj = NewsObject(
        analysis_id, number, None, None, None, None, None, locality, address, cad,
        None, None, None, datetime(2026, 10, 29, 12, tzinfo=TZ), None,
        tuple(all_cads), None,
    )
    return MonitoringSourceRecord(
        obj, AnalysisData(analysis_id, incident, url, "Готово", None)
    )


def run(records, sources):
    return match_news(build_registry_index(records), sources)


class CadastralNormalizationTests(unittest.TestCase):
    def test_exact_and_spaces(self):
        self.assertEqual("66:41:0303001:123", normalize_cadastral_number(" 66 : 41 : 0303001 : 123 "))

    def test_invalid_and_quarter_are_not_full_numbers(self):
        self.assertIsNone(normalize_cadastral_number("66:41:0303001"))
        self.assertIsNone(normalize_cadastral_number("не номер"))

    def test_multiple_numbers_are_extracted_separately(self):
        value = "66:41:0303001:123; 66:41:0303001:456"
        self.assertEqual(2, len(extract_cadastral_numbers(value)))
        self.assertIsNone(normalize_cadastral_number(value))

    def test_quarter(self):
        self.assertEqual("66:41:0303001", cadastral_quarter("66:41:0303001:123"))


class AddressNormalizationTests(unittest.TestCase):
    def test_abbreviations_and_punctuation(self):
        left = normalize_address("г. Екатеринбург, ул. Ленина, д. 1")
        right = normalize_address("город Екатеринбург улица Ленина дом 1")
        self.assertEqual(left.key, right.key)
        self.assertTrue(left.is_identifiable)

    def test_yo_and_other_unambiguous_abbreviations(self):
        left = normalize_address("г. Орёл, пр-т Мира, д. 2, корп. 1, стр. 3")
        right = normalize_address("город Орел проспект Мира дом 2 корпус 1 строение 3")
        self.assertEqual(left.key, right.key)

    def test_insufficient_address(self):
        self.assertFalse(normalize_address("город Екатеринбург улица Ленина").is_identifiable)
        self.assertFalse(normalize_address("дом 1").is_identifiable)

    def test_bare_locality_and_street_order_have_one_structured_key(self):
        excel = normalize_address(
            "Москва, Профсоюзная улица, дом 123А, строение 15"
        )
        google = normalize_address(
            "ул. Профсоюзная, д. 123А, стр. 15", locality="Москва"
        )
        self.assertTrue(excel.is_identifiable)
        self.assertTrue(google.is_identifiable)
        self.assertEqual(excel.key, google.key)

    def test_locality_is_not_duplicated_when_already_in_address(self):
        included = normalize_address(
            "Москва, улица Профсоюзная, дом 123А", locality="Москва"
        )
        separate = normalize_address(
            "улица Профсоюзная, дом 123А", locality="Москва"
        )
        self.assertEqual(included.key, separate.key)


class MatchingTests(unittest.TestCase):
    base_address = "город Екатеринбург улица Ленина дом 1"

    def test_exact_cadastral_match(self):
        result = run([collateral("x", "66:41:0303001:123")], [news(cad="66:41:0303001:123")])
        self.assertEqual(PledgeStatus.PLEDGED, result.per_news[0].status)
        self.assertEqual((MatchReason.CADASTRAL,), result.exact_matches[0].reasons)

    def test_different_full_numbers_do_not_match(self):
        result = run([collateral("x", "66:41:0303001:123")], [news(cad="66:41:0303001:124")])
        self.assertEqual(PledgeStatus.NOT_PLEDGED, result.per_news[0].status)

    def test_multiple_news_numbers(self):
        result = run(
            [collateral("x", "66:41:0303001:456")],
            [news(all_cads=("66:41:0303001:123", "66:41:0303001:456"))],
        )
        self.assertEqual(1, len(result.exact_matches))

    def test_normalized_address_match(self):
        result = run(
            [collateral("x", address=self.base_address)],
            [news(address="г. Екатеринбург, ул. Ленина, д. 1")],
        )
        self.assertEqual((MatchReason.ADDRESS,), result.exact_matches[0].reasons)

    def test_different_house_corpus_and_city_do_not_match(self):
        records = [collateral("x", address=self.base_address + " корпус 1")]
        for address in (
            "город Екатеринбург улица Ленина дом 2 корпус 1",
            "город Екатеринбург улица Ленина дом 1 корпус 2",
            "город Пермь улица Ленина дом 1 корпус 1",
        ):
            self.assertEqual(PledgeStatus.NOT_PLEDGED, run(records, [news(address=address)]).per_news[0].status)

    def test_different_structure_does_not_match(self):
        records = [collateral("x", address=self.base_address + " строение 1")]
        result = run(records, [news(address=self.base_address + " строение 2")])
        self.assertEqual(PledgeStatus.NOT_PLEDGED, result.per_news[0].status)

    def test_same_street_house_in_different_localities_does_not_match(self):
        records = [
            collateral("x", address="Москва, Профсоюзная улица, дом 123А")
        ]
        result = run(
            records,
            [news(address="Профсоюзная улица, дом 123А", locality="Казань")],
        )
        self.assertEqual(PledgeStatus.NOT_PLEDGED, result.per_news[0].status)

    def test_no_identification_data(self):
        for source in (news(), news(cad="ошибка", address="город Екатеринбург")):
            self.assertEqual(PledgeStatus.NO_IDENTIFICATION_DATA, run([], [source]).per_news[0].status)

    def test_cadastral_and_address_same_record(self):
        result = run(
            [collateral("x", "66:41:0303001:123", self.base_address)],
            [news(cad="66:41:0303001:123", address=self.base_address)],
        )
        self.assertEqual((MatchReason.CADASTRAL_AND_ADDRESS,), result.exact_matches[0].reasons)
        self.assertFalse(result.per_news[0].identifier_conflict)

    def test_control_address_match_with_different_cadastral_number(self):
        result = run(
            [
                collateral(
                    "ID-11111-111",
                    "77:06:0008009:1012",
                    "Москва, Профсоюзная улица, дом 123А, строение 15",
                )
            ],
            [
                news(
                    cad="77:06:0008009:1026",
                    locality="Москва",
                    address="Профсоюзная улица, дом 123А, строение 15",
                )
            ],
        )
        self.assertEqual(PledgeStatus.PLEDGED, result.per_news[0].status)
        self.assertEqual((MatchReason.ADDRESS,), result.exact_matches[0].reasons)
        self.assertFalse(result.quarter_matches)
        self.assertFalse(result.per_news[0].identifier_conflict)
        self.assertTrue(
            any("кадастровый номер залога отличается" in warning for warning in result.per_news[0].warnings)
        )
        self.assertEqual(1, result.diagnostics.address_cadastral_mismatch_count)

    def test_identifier_conflict_is_visible(self):
        result = run(
            [
                collateral("cad", "66:41:0303001:123", "город Екатеринбург улица Мира дом 9", 2),
                collateral("addr", "66:41:0303001:999", self.base_address, 3),
            ],
            [news(cad="66:41:0303001:123", address=self.base_address)],
        )
        item = result.per_news[0]
        self.assertTrue(item.identifier_conflict)
        self.assertNotEqual(item.cadastral_match_keys, item.address_match_keys)
        self.assertEqual(2, len(result.exact_matches))

    def test_multiple_collaterals_for_one_key_are_all_kept(self):
        result = run(
            [collateral("x1", "66:41:0303001:123", row=2), collateral("x2", "66:41:0303001:123", row=3)],
            [news(cad="66:41:0303001:123")],
        )
        self.assertEqual(2, len(result.exact_matches))

    def test_one_collateral_aggregates_news_types_and_urls(self):
        records = [collateral("x", "66:41:0303001:123")]
        sources = [
            news(cad="66:41:0303001:123", analysis_id="a1", incident="пожар", url="u1"),
            news(cad="66:41:0303001:123", analysis_id="a2", incident="взрыв", url="u2"),
        ]
        result = run(records, sources)
        self.assertEqual(1, len(result.exact_matches))
        self.assertEqual(("взрыв", "пожар"), result.exact_matches[0].incident_types)
        self.assertEqual(2, len(result.exact_matches[0].related_news))

    def test_quarter_matches_exclude_exact_for_same_news(self):
        records = [
            collateral("a", "66:41:0303001:123", row=2),
            collateral("b", "66:41:0303001:456", row=3),
            collateral("c", "66:41:0303001:789", row=4),
        ]
        result = run(records, [news(cad="66:41:0303001:123")])
        self.assertEqual(1, len(result.exact_matches))
        self.assertEqual({"b", "c"}, {x.collateral.object_id for x in result.quarter_matches})

    def test_no_quarter_match(self):
        result = run([collateral("x", "59:01:0000001:1")], [news(cad="66:41:0303001:123")])
        self.assertFalse(result.quarter_matches)

    def test_global_exact_is_removed_from_quarter_section(self):
        records = [collateral("x", "66:41:0303001:123")]
        sources = [news(cad="66:41:0303001:999", analysis_id="a1"), news(cad="66:41:0303001:123", analysis_id="a2")]
        result = run(records, sources)
        self.assertEqual(1, len(result.exact_matches))
        self.assertFalse(result.quarter_matches)

    def test_duplicate_or_missing_ids_use_separate_row_keys(self):
        idx = build_registry_index([
            collateral("same", "66:41:0303001:123", row=2),
            collateral("same", "66:41:0303001:123", row=3),
            collateral(None, "66:41:0303001:123", row=4),
        ])
        result = match_news(idx, [news(cad="66:41:0303001:123")])
        self.assertEqual({"row:2", "row:3", "row:4"}, {x.collateral_key for x in result.exact_matches})
        self.assertTrue(result.diagnostics.warnings)

    def test_result_is_independent_of_news_order(self):
        records = [collateral("x", "66:41:0303001:123")]
        sources = [news(cad="66:41:0303001:123", analysis_id="b"), news(cad="66:41:0303001:123", analysis_id="a")]
        left, right = run(records, sources), run(records, reversed(sources))
        self.assertEqual(left.exact_matches, right.exact_matches)
        self.assertEqual(left.per_news, right.per_news)

    def test_empty_period_and_incomplete_registry(self):
        idx = build_registry_index([collateral(None, "bad", "город Екатеринбург", row=2)])
        result = match_news(idx, [])
        self.assertFalse(result.per_news)
        self.assertEqual(1, result.diagnostics.invalid_cadastral_rows)
        self.assertEqual(1, result.diagnostics.unidentifiable_address_rows)


if __name__ == "__main__":
    unittest.main()
