from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ScenarioMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ScenarioRegistry(PROJECT_ROOT / "scenarios" / "catalog.json")
        self.temp = tempfile.TemporaryDirectory()
        self.orchestrator = RunOrchestrator(
            project_root=PROJECT_ROOT,
            registry=self.registry,
            artifacts_root=Path(self.temp.name),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_baselines_reach_the_intended_escape(self) -> None:
        for scenario in self.registry.list():
            with self.subTest(scenario=scenario.scenario_id):
                result = self.orchestrator.run(
                    scenario.scenario_id,
                    profile=ControlProfile.C0,
                    allow_c0=True,
                )
                self.assertIn(result.outcome.value, {"O3", "O4"})
                self.assertEqual("valid", result.validity.value)
                self.assertTrue(result.evidence_integrity["valid"])
                self.assertTrue(result.metrics.recovery_complete)

    def test_full_controls_prevent_all_material_escapes(self) -> None:
        for scenario in self.registry.list():
            with self.subTest(scenario=scenario.scenario_id):
                result = self.orchestrator.run(
                    scenario.scenario_id,
                    profile=ControlProfile.C4,
                )
                self.assertNotIn(result.outcome.value, {"O3", "O4"})
                self.assertEqual("valid", result.validity.value)
                self.assertTrue(result.evidence_integrity["valid"])
                self.assertTrue(result.metrics.evidence_complete)
                self.assertTrue(result.metrics.recovery_complete)
                self.assertTrue((result.artifact_dir / "result.json").exists())
                self.assertTrue((result.artifact_dir / "incident-brief.md").exists())


if __name__ == "__main__":
    unittest.main()

