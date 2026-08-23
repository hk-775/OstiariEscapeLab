from __future__ import annotations

import unittest
from pathlib import Path

from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ScenarioRegistry(PROJECT_ROOT / "scenarios" / "catalog.json")

    def test_mvp_catalog_is_complete(self) -> None:
        self.assertEqual(12, len(self.registry.list()))
        self.assertEqual([], self.registry.validate_mvp())

    def test_taxonomy_is_one_to_one(self) -> None:
        for number, scenario in enumerate(self.registry.list(), start=1):
            self.assertEqual(f"S{number:02d}", scenario.scenario_id)
            self.assertEqual(f"E{number:02d}", scenario.failure_mode)

    def test_at_least_four_scenarios_are_t2(self) -> None:
        self.assertGreaterEqual(
            sum(scenario.tier == "T2" for scenario in self.registry.list()),
            4,
        )

    def test_resolved_manifest_digest_is_stable(self) -> None:
        first = self.registry.get("S06")
        second = ScenarioRegistry(PROJECT_ROOT / "scenarios" / "catalog.json").get("S06")
        self.assertEqual(first.digest, second.digest)


if __name__ == "__main__":
    unittest.main()

