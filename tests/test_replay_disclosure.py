from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from escape_lab.disclosure import (
    append_disclosure_status,
    verify_disclosure_ledger,
)
from escape_lab.experiment import replay_run
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReplayAndDisclosureTests(unittest.TestCase):
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

    def test_frozen_run_replays(self) -> None:
        original = self.orchestrator.run("S06", profile=ControlProfile.C4, seed=41)
        _replay, check = replay_run(self.orchestrator, original.artifact_dir)
        self.assertTrue(check["reproducible"])

    def test_disclosure_ledger_is_hash_chained(self) -> None:
        result = self.orchestrator.run("S01", profile=ControlProfile.C4)
        append_disclosure_status(
            result.artifact_dir,
            status="acknowledged",
            note="Maintainer received the private report.",
        )
        append_disclosure_status(
            result.artifact_dir,
            status="remediated",
            note="Candidate fix replayed successfully.",
        )
        verification = verify_disclosure_ledger(
            result.artifact_dir / "disclosure.jsonl"
        )
        self.assertTrue(verification["valid"])
        self.assertEqual(2, verification["records"])


if __name__ == "__main__":
    unittest.main()

