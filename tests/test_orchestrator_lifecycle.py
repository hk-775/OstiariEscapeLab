from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from escape_lab.agents import AgentAdapterError, ScriptedAgentSession
from escape_lab.models import ControlProfile, Scenario
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _StartupFailingFactory:
    def start(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
    ) -> ScriptedAgentSession:
        del scenario, seed, run_id
        raise AgentAdapterError("synthetic adapter startup failure")


class _CloseFailingSession(ScriptedAgentSession):
    def close(self) -> None:
        raise RuntimeError("synthetic adapter close failure")


class _CloseFailingFactory:
    def start(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
    ) -> ScriptedAgentSession:
        del run_id
        return _CloseFailingSession(scenario, seed=seed)


class OrchestratorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.artifacts = Path(self.temporary.name) / "artifacts"
        self.registry = ScenarioRegistry(
            PROJECT_ROOT / "scenarios" / "catalog.json"
        )

    def _orchestrator(self, agent_factory: object) -> RunOrchestrator:
        return RunOrchestrator(
            project_root=PROJECT_ROOT,
            registry=self.registry,
            artifacts_root=self.artifacts,
            agent_factory=agent_factory,
        )

    def test_startup_failure_tears_down_range_and_records_error(self) -> None:
        with self.assertRaisesRegex(
            AgentAdapterError,
            "synthetic adapter startup failure",
        ):
            self._orchestrator(_StartupFailingFactory()).run(
                "S06",
                profile=ControlProfile.C4,
            )

        ranges = self.artifacts / "ranges"
        self.assertTrue(ranges.is_dir())
        self.assertEqual([], list(ranges.iterdir()))

        event_paths = list((self.artifacts / "runs").glob("*/events.jsonl"))
        self.assertEqual(1, len(event_paths))
        events = [
            json.loads(line)
            for line in event_paths[0].read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual("infrastructure_error", events[-1]["event_type"])
        self.assertEqual("run_setup", events[-1]["data"]["stage"])

    def test_close_failure_does_not_mask_a_completed_report(self) -> None:
        result = self._orchestrator(_CloseFailingFactory()).run(
            "S06",
            profile=ControlProfile.C4,
        )

        self.assertEqual("valid", result.validity.value)
        self.assertTrue((result.artifact_dir / "result.json").is_file())
        self.assertEqual([], list((self.artifacts / "ranges").iterdir()))


if __name__ == "__main__":
    unittest.main()
