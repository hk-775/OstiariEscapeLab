from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from escape_lab.evidence import verify_evidence
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvidenceTests(unittest.TestCase):
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

    def test_hash_chain_verifies(self) -> None:
        result = self.orchestrator.run("S01", profile=ControlProfile.C4)
        verification = verify_evidence(result.artifact_dir / "events.jsonl")
        self.assertTrue(verification["valid"])
        self.assertGreater(verification["event_count"], 0)

    def test_tampering_is_detected(self) -> None:
        result = self.orchestrator.run("S01", profile=ControlProfile.C4)
        path = result.artifact_dir / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["data"]["profile"] = "tampered"
        lines[0] = json.dumps(first, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        verification = verify_evidence(path)
        self.assertFalse(verification["valid"])
        self.assertTrue(verification["errors"])


if __name__ == "__main__":
    unittest.main()

