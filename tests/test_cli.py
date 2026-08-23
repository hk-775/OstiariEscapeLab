from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from escape_lab.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_validate_command(self) -> None:
        self.assertEqual(
            0,
            main(["--project-root", str(PROJECT_ROOT), "validate"]),
        )

    def test_three_scenario_demo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                0,
                main(
                    [
                        "--project-root",
                        str(PROJECT_ROOT),
                        "--artifacts-root",
                        temporary,
                        "demo",
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()

