from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from monitoring_bot_v3.collateral import (
    CollateralExcelError,
    load_collateral_records,
    scan_collateral_excel,
)


class CollateralExcelTests(unittest.TestCase):
    def make_file(self, headers, rows):
        # Системный %TEMP% в управляемом Windows sandbox может быть недоступен.
        # Каталог тестов находится внутри разрешённого workspace и одинаково
        # работает на Windows/Linux.
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        path = Path(temp.name) / "registry.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(headers)
        for row in rows:
            sheet.append(row)
        workbook.save(path)
        workbook.close()
        self.addCleanup(temp.cleanup)
        return path

    def test_columns_are_found_by_header_not_position(self):
        path = self.make_file(
            ["ID", "Кадастровый номер", "Адрес", "Лишняя колонка"],
            [
                ["0001", "66:41:0101010:0007", "г. Екатеринбург, дом 1", "x"],
                ["0002", None, "", "y"],
            ],
        )
        records = load_collateral_records(path)
        self.assertEqual("0001", records[0].object_id)
        self.assertEqual("66:41:0101010:0007", records[0].cadastral_number)
        self.assertEqual("г. Екатеринбург, дом 1", records[0].address)
        self.assertIsNone(records[1].address)
        self.assertIsNone(records[1].cadastral_number)

    def test_scan_counts_empty_values(self):
        path = self.make_file(
            ["адрес", "кадастровый", "ID"],
            [["Адрес 1", "01:02:0000003:4", "id-1"], [None, None, "id-2"]],
        )
        result = scan_collateral_excel(path)
        self.assertEqual(2, result.total_rows)
        self.assertEqual(2, result.rows_with_id)
        self.assertEqual(1, result.rows_with_address)
        self.assertEqual(1, result.rows_with_cadastral)

    def test_missing_required_header_is_clear_error(self):
        path = self.make_file(["адрес", "ID"], [["Адрес", "1"]])
        with self.assertRaisesRegex(CollateralExcelError, "обязательная колонка"):
            load_collateral_records(path)

    def test_source_file_is_not_modified(self):
        path = self.make_file(
            ["адрес", "кадастровый", "ID"],
            [["Адрес", "01:02:3:4", "0000000000000001"]],
        )
        before = path.read_bytes()
        records = load_collateral_records(path)
        after = path.read_bytes()
        self.assertEqual("0000000000000001", records[0].object_id)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

