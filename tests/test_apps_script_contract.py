from __future__ import annotations

import unittest
from pathlib import Path


class AppsScriptContractTests(unittest.TestCase):
    def test_schedule_reads_raw_and_display_values_with_diagnostics(self):
        source = (Path(__file__).resolve().parents[1] / "apps_script" / "Code.gs").read_text(encoding="utf-8")
        self.assertIn("getValues()", source)
        self.assertIn("getDisplayValues()", source)
        self.assertIn("Asia/Yekaterinburg", source)
        self.assertIn("invalid_schedule_time_row_", source)
        self.assertIn("padStart(2, '0')", source)


if __name__ == "__main__":
    unittest.main()
