from __future__ import annotations

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from escape_lab.gate import load_baseline, run_gate
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.artifacts = Path(self.temporary.name) / "artifacts"
        self.orchestrator = RunOrchestrator(
            project_root=PROJECT_ROOT,
            registry=ScenarioRegistry(
                PROJECT_ROOT / "scenarios" / "catalog.json"
            ),
            artifacts_root=self.artifacts,
        )

    def _scripted_baseline(self) -> dict:
        baseline = copy.deepcopy(
            load_baseline(PROJECT_ROOT / "baselines" / "first-product.json")
        )
        baseline["name"] = "scripted-gate-test"
        baseline["requirements"]["required_agent_adapter"] = "scripted-agent"
        baseline["requirements"]["minimum_model_turns"] = 0
        return baseline

    def test_passing_gate_writes_all_ci_reports(self) -> None:
        output = Path(self.temporary.name) / "passing-gate"
        result = run_gate(
            self.orchestrator,
            self._scripted_baseline(),
            output_dir=output,
        )

        self.assertTrue(result.passed)
        for filename in ("gate.json", "gate.md", "gate.html", "junit.xml"):
            self.assertTrue((output / filename).is_file())
        payload = json.loads((output / "gate.json").read_text(encoding="utf-8"))
        self.assertEqual("pass", payload["status"])
        suite = ET.parse(output / "junit.xml").getroot()
        self.assertEqual("0", suite.attrib["failures"])

    def test_agent_adapter_regression_fails_gate(self) -> None:
        output = Path(self.temporary.name) / "failing-gate"
        result = run_gate(
            self.orchestrator,
            load_baseline(PROJECT_ROOT / "baselines" / "first-product.json"),
            output_dir=output,
        )

        self.assertFalse(result.passed)
        self.assertTrue(
            any(
                reason.startswith("required_agent_adapter:")
                for reason in result.report["reasons"]
            )
        )
        suite = ET.parse(output / "junit.xml").getroot()
        self.assertGreater(int(suite.attrib["failures"]), 0)

    def test_default_output_directories_are_collision_resistant(self) -> None:
        baseline = self._scripted_baseline()
        first = run_gate(self.orchestrator, baseline)
        second = run_gate(self.orchestrator, baseline)

        self.assertNotEqual(first.output_dir, second.output_dir)
        self.assertTrue((first.output_dir / "gate.json").is_file())
        self.assertTrue((second.output_dir / "gate.json").is_file())

    def test_gate_can_require_control_and_range_backends(self) -> None:
        baseline = self._scripted_baseline()
        baseline["requirements"]["required_control_backend"] = "reference"
        baseline["requirements"]["required_range_backend"] = "synthetic"
        output = Path(self.temporary.name) / "backend-gate"

        result = run_gate(
            self.orchestrator,
            baseline,
            output_dir=output,
        )

        self.assertTrue(result.passed)
        checks = {
            check["name"]: check
            for check in result.report["checks"]
        }
        self.assertTrue(checks["required_control_backend"]["passed"])
        self.assertTrue(checks["required_range_backend"]["passed"])


if __name__ == "__main__":
    unittest.main()
