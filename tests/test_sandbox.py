from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from escape_lab.models import ActionRequest, ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.range import SyntheticRange
from escape_lab.registry import ScenarioRegistry
from escape_lab.sandbox import (
    DockerSandboxConfig,
    DockerSandboxFactory,
    DockerSandboxRange,
    SandboxError,
    docker_worker_command,
    sandbox_environment_digest,
)
from escape_lab.sandbox_worker import SandboxWorker
from escape_lab.util import to_primitive

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _RecordedSyntheticRange(SyntheticRange):
    @property
    def metadata(self) -> dict[str, object]:
        return {
            **super().metadata,
            "backend": "recorded-test-range",
        }


class _RecordedRangeFactory:
    def create(
        self,
        scenario,
        range_base: Path,
        run_id: str,
    ) -> _RecordedSyntheticRange:
        return _RecordedSyntheticRange(scenario, range_base, run_id)


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ScenarioRegistry(
            PROJECT_ROOT / "scenarios" / "catalog.json"
        )

    def test_worker_command_applies_required_isolation(self) -> None:
        command = docker_worker_command(
            docker_binary="docker",
            image_reference="sha256:" + "a" * 64,
            container_name="escape-lab-test",
            runtime="runsc",
            memory="256m",
            cpus="0.5",
            pids_limit=64,
            range_tmpfs="64m",
            temp_tmpfs="32m",
            run_id="test-run",
        )

        rendered = " ".join(command)
        self.assertIn("--runtime runsc", rendered)
        self.assertIn("--network none", rendered)
        self.assertIn("--ipc none", rendered)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("no-new-privileges:true", command)
        self.assertIn("--pull never", rendered)
        self.assertIn("--user 65532:65532", rendered)
        self.assertIn("--ulimit nofile=256:256", rendered)
        self.assertIn("/range:rw,noexec,nosuid,nodev", rendered)
        self.assertNotIn("--mount", command)
        self.assertNotIn("--volume", command)
        self.assertEqual(
            ["python", "-m", "escape_lab.sandbox_worker"],
            command[-3:],
        )

    def test_gvisor_runtime_is_required_fail_closed(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="29.2.0\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout='{"runc": {}}\n',
                stderr="",
            ),
        ]
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "escape_lab.sandbox.subprocess.run",
                side_effect=responses,
            ),
        ):
            with self.assertRaisesRegex(
                SandboxError,
                "runtime 'runsc' is unavailable",
            ):
                DockerSandboxRange(
                    self.registry.get("S02"),
                    Path(temporary),
                    "test-run",
                    config=DockerSandboxConfig(
                        image="escape-lab-range:test",
                        runtime="runsc",
                        docker_binary="/bin/echo",
                    ),
                )

    def test_environment_digest_excludes_ephemeral_container_identity(self) -> None:
        base = {
            "backend": "docker",
            "runtime": "docker-default",
            "docker_server_version": "29.2.0",
            "image_id": "sha256:" + "a" * 64,
            "container_name": "escape-lab-first-run",
            "network": "none",
            "ipc": "none",
            "root_filesystem": "read-only",
            "user": "65532:65532",
            "capabilities": "none",
            "no_new_privileges": True,
            "limits": {
                "memory": "256m",
                "cpus": "0.5",
                "pids": 64,
                "nofile": "256:256",
                "range_tmpfs": "64m",
                "temp_tmpfs": "32m",
            },
            "isolation_probe": {
                "passed": True,
                "checks": {"network_isolated": True},
                "interface_states": {"lo": "unknown"},
            },
        }
        replay = {
            **base,
            "container_name": "escape-lab-replay",
            "isolation_probe": {
                **base["isolation_probe"],
                "interface_states": {
                    "erspan0": "down",
                    "lo": "unknown",
                },
            },
        }

        self.assertEqual(
            sandbox_environment_digest("inner", base),
            sandbox_environment_digest("inner", replay),
        )

    def test_worker_owns_preview_execution_and_teardown(self) -> None:
        scenario = self.registry.get("S06")
        with tempfile.TemporaryDirectory() as temporary:
            worker = SandboxWorker(Path(temporary))
            initialized = worker.handle(
                "init",
                {
                    "scenario": scenario.data,
                    "scenario_digest": scenario.digest,
                    "run_id": "worker-test",
                },
            )
            self.assertIn("environment_digest", initialized)

            request = ActionRequest(
                request_id="request-1",
                trajectory_id="worker-test",
                parent_id=None,
                actor="agent",
                action="file.read",
                params={"resource": "/fixtures/customer-104"},
                description="Read the authorized fixture",
                expected_authorized=True,
                prohibited_attempt=False,
            )
            preview = worker.handle(
                "preview",
                {"request": to_primitive(request)},
            )
            self.assertIn("protected", preview["labels"])
            result = worker.handle(
                "execute",
                {
                    "request": to_primitive(request),
                    "params": None,
                },
            )
            self.assertEqual(
                "/fixtures/customer-104",
                result["resource"],
            )
            teardown = worker.handle("teardown", {})
            self.assertTrue(teardown["complete"])

    def test_run_records_selected_range_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=self.registry,
                artifacts_root=Path(temporary),
                range_factory=_RecordedRangeFactory(),
            )
            result = orchestrator.run(
                "S06",
                profile=ControlProfile.C4,
            )

            self.assertEqual(
                "recorded-test-range",
                result.range_configuration["backend"],
            )
            manifest = (
                result.artifact_dir / "run-manifest.json"
            ).read_text(encoding="utf-8")
            self.assertIn('"backend": "recorded-test-range"', manifest)

    @unittest.skipUnless(
        os.environ.get("ESCAPE_LAB_SANDBOX_IMAGE"),
        "Set ESCAPE_LAB_SANDBOX_IMAGE to run the real OCI range integration",
    )
    def test_real_oci_range_runs_and_tears_down(self) -> None:
        image = os.environ["ESCAPE_LAB_SANDBOX_IMAGE"]
        runtime = os.environ.get("ESCAPE_LAB_SANDBOX_RUNTIME")
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=self.registry,
                artifacts_root=Path(temporary),
                range_factory=DockerSandboxFactory(
                    DockerSandboxConfig(
                        image=image,
                        runtime=runtime,
                    )
                ),
            )
            result = orchestrator.run(
                "S02",
                profile=ControlProfile.C4,
            )
            replay = orchestrator.run(
                "S02",
                profile=ControlProfile.C4,
            )

            expected_backend = (
                "gvisor"
                if runtime and runtime.startswith("runsc")
                else "docker"
            )
            self.assertEqual(
                expected_backend,
                result.range_configuration["backend"],
            )
            self.assertTrue(
                result.range_configuration["isolation_probe"]["passed"]
            )
            self.assertTrue(result.teardown["container_removed"])
            self.assertTrue(result.metrics.recovery_complete)
            self.assertNotEqual(
                result.range_configuration["container_name"],
                replay.range_configuration["container_name"],
            )
            self.assertEqual(
                result.environment_digest,
                replay.environment_digest,
            )
            self.assertTrue(replay.teardown["container_removed"])


if __name__ == "__main__":
    unittest.main()
