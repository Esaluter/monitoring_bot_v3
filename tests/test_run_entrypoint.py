from __future__ import annotations

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch


class RunEntrypointTests(unittest.TestCase):
    def test_run_py_delegates_to_existing_cli(self):
        path = Path(__file__).resolve().parents[1] / "run.py"
        with patch("monitoring_bot_v3.cli.main", return_value=0) as main:
            with self.assertRaisesRegex(SystemExit, "0"):
                runpy.run_path(str(path), run_name="__main__")
        main.assert_called_once_with(["run-telegram"])


if __name__ == "__main__":
    unittest.main()
