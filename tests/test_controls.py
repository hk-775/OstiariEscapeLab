from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from escape_lab.controls import ScopedApprovalService
from escape_lab.models import ActionPreview, ActionRequest, ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ControlTests(unittest.TestCase):
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

    def test_scope_bound_approval_rejects_parameter_change(self) -> None:
        service = ScopedApprovalService(secret=b"test-secret", expiry_seconds=60)
        request = ActionRequest(
            request_id="request-1",
            trajectory_id="trajectory-1",
            parent_id=None,
            actor="agent",
            action="workspace.save",
            params={"resource": "/workspace/result", "payload": "safe"},
            description="",
            expected_authorized=True,
            prohibited_attempt=False,
        )
        preview = ActionPreview(
            declared_resource="/workspace/result",
            effective_resource="/workspace/result",
        )
        grant = service.approve(run_id="run-1", request=request, preview=preview)
        self.assertTrue(
            service.verify(grant, run_id="run-1", request=request, preview=preview)
        )

        tampered = ActionRequest(
            request_id=request.request_id,
            trajectory_id=request.trajectory_id,
            parent_id=request.parent_id,
            actor=request.actor,
            action=request.action,
            params={"resource": "/workspace/result", "payload": "changed"},
            description=request.description,
            expected_authorized=True,
            prohibited_attempt=False,
        )
        self.assertFalse(
            service.verify(grant, run_id="run-1", request=tampered, preview=preview)
        )

    def test_trajectory_profile_stops_approval_composition(self) -> None:
        static = self.orchestrator.run("S08", profile=ControlProfile.C1)
        trajectory = self.orchestrator.run("S08", profile=ControlProfile.C3)
        self.assertEqual("O3", static.outcome.value)
        self.assertEqual("O1", trajectory.outcome.value)

    def test_full_profile_redacts_source_to_sink_payload(self) -> None:
        result = self.orchestrator.run("S06", profile=ControlProfile.C4)
        self.assertEqual("O1", result.outcome.value)
        self.assertTrue(result.metrics.task_completed)
        self.assertEqual(1, result.metrics.prohibited_attempts_prevented)


if __name__ == "__main__":
    unittest.main()

