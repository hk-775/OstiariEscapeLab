from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from escape_lab.agent_sandbox import (
    GVisorAgentConfig,
    GVisorFixtureAgentFactory,
    GVisorFixtureAgentSession,
    agent_runtime_digest,
    container_contract_probe,
    gvisor_agent_command,
)
from escape_lab.agent_worker import agent_isolation_probe, identity_probe
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AgentSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ScenarioRegistry(
            PROJECT_ROOT / "scenarios" / "catalog.json"
        )

    def test_command_requires_gvisor_and_forwards_no_identity(self) -> None:
        command = gvisor_agent_command(
            docker_binary="docker",
            image_reference="sha256:" + "a" * 64,
            container_name="escape-lab-agent-test",
            runtime="runsc",
            memory="512m",
            cpus="1.0",
            pids_limit=64,
            workspace_tmpfs="64m",
            temp_tmpfs="64m",
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
        self.assertIn("--entrypoint python", rendered)
        self.assertNotIn("--mount", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("--env-file", command)
        self.assertNotIn("API_KEY", rendered)
        self.assertNotIn("TOKEN", rendered)
        self.assertEqual(
            [
                "sha256:" + "a" * 64,
                "-m",
                "escape_lab.agent_worker",
            ],
            command[-3:],
        )

    def test_config_rejects_non_gvisor_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "runsc"):
            GVisorAgentConfig(
                image="escape-lab-agent:test",
                runtime="runc",
            )

    def test_runtime_digest_excludes_ephemeral_container_name(self) -> None:
        base = {
            "backend": "gvisor",
            "runtime": "runsc",
            "docker_server_version": "29.2.0",
            "image_id": "sha256:" + "a" * 64,
            "container_name": "first",
            "network": "none",
            "provider_transport": "in-process",
            "provider_auth": "none",
            "user": "65532:65532",
            "capabilities": "none",
            "root_filesystem": "read-only",
            "limits": {"memory": "512m"},
            "isolation_probe": {
                "passed": True,
                "checks": {"network_isolated": True},
            },
        }
        replay = {**base, "container_name": "second"}

        self.assertEqual(
            agent_runtime_digest(base),
            agent_runtime_digest(replay),
        )

    def test_identity_probe_detects_provider_key(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "must-not-enter-agent"},
            clear=True,
        ):
            result = identity_probe()

        self.assertFalse(result["passed"])
        self.assertEqual(
            ["OPENAI_API_KEY"],
            result["environment_variables"],
        )

    def test_identity_probe_does_not_treat_inaccessible_files_as_accessible(
        self,
    ) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "escape_lab.agent_worker.Path.is_file",
                side_effect=PermissionError,
            ),
        ):
            result = identity_probe()

        self.assertTrue(result["passed"])
        self.assertEqual([], result["paths"])
        self.assertEqual(5, len(result["inaccessible_paths"]))

    def test_agent_probe_accepts_gvisor_network_and_mount_observations(
        self,
    ) -> None:
        normalized = agent_isolation_probe(
            {
                "checks": {
                    "non_root": True,
                    "capabilities_dropped": True,
                    "no_new_privileges": False,
                    "root_read_only": True,
                    "network_isolated": False,
                    "range_tmpfs_writable": True,
                    "range_tmpfs_hardened": False,
                    "temp_tmpfs_hardened": False,
                },
                "interface_states": {},
                "ipv4_non_loopback_routes": [],
                "ipv6_non_loopback_routes": [],
                "mount_options": {
                    "/": ["ro"],
                    "/range": ["rw", "noexec", "nosuid"],
                    "/tmp": ["rw", "noexec", "nosuid"],
                },
            }
        )

        self.assertTrue(normalized["passed"])
        self.assertTrue(normalized["checks"]["network_isolated"])
        self.assertFalse(
            normalized["raw_checks"]["no_new_privileges"]
        )
        self.assertIn(
            "no_new_privileges",
            normalized["host_verification_required"],
        )

    def test_container_contract_requires_runsc_no_network_and_no_identity(
        self,
    ) -> None:
        config = GVisorAgentConfig(image="escape-lab-agent:test")
        payload = {
            "HostConfig": {
                "Runtime": "runsc",
                "NetworkMode": "none",
                "IpcMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Privileged": False,
                "Binds": None,
                "VolumesFrom": None,
                "Devices": [],
                "DeviceRequests": None,
                "DeviceCgroupRules": None,
                "PortBindings": {},
                "PublishAllPorts": False,
                "Tmpfs": {
                    "/range": "rw,noexec,nosuid,nodev,size=64m",
                    "/tmp": "rw,noexec,nosuid,nodev,size=64m",
                },
                "Memory": 536_870_912,
                "NanoCpus": 1_000_000_000,
                "PidsLimit": 64,
                "Ulimits": [
                    {"Name": "nofile", "Soft": 256, "Hard": 256}
                ],
                "AutoRemove": True,
            },
            "Config": {
                "User": "65532:65532",
                "Env": ["HOME=/range", "GPG_KEY=public-fingerprint"],
                "Volumes": None,
                "ExposedPorts": None,
            },
            "Mounts": [],
            "NetworkSettings": {"Networks": {"none": {}}},
        }

        contract = container_contract_probe(payload, config)
        self.assertTrue(contract["passed"])

        payload["Config"]["Env"].append("OPENAI_API_KEY=must-not-enter")
        rejected = container_contract_probe(payload, config)
        self.assertFalse(rejected["passed"])
        self.assertFalse(
            rejected["checks"]["identity_environment_absent"]
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
        with patch(
            "escape_lab.agent_sandbox.subprocess.run",
            side_effect=responses,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "runtime 'runsc' is unavailable",
            ):
                GVisorFixtureAgentSession(
                    self.registry.get("S06"),
                    seed=1,
                    run_id="test-run",
                    config=GVisorAgentConfig(
                        image="escape-lab-agent:test",
                        docker_binary="/bin/echo",
                    ),
                )

    @unittest.skipUnless(
        os.environ.get("ESCAPE_LAB_AGENT_IMAGE"),
        "Set ESCAPE_LAB_AGENT_IMAGE to run the real gVisor agent integration",
    )
    def test_real_gvisor_fixture_agent_runs_without_identity(self) -> None:
        image = os.environ["ESCAPE_LAB_AGENT_IMAGE"]
        runtime = os.environ.get("ESCAPE_LAB_AGENT_RUNTIME", "runsc")
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=self.registry,
                artifacts_root=Path(temporary),
                agent_factory=GVisorFixtureAgentFactory(
                    GVisorAgentConfig(
                        image=image,
                        runtime=runtime,
                    )
                ),
            )
            result = orchestrator.run(
                "S06",
                profile=ControlProfile.C4,
                seed=17,
            )

        runtime_metadata = result.agent_configuration["runtime"]
        self.assertEqual("axonllm", result.agent_configuration["adapter"])
        self.assertEqual("offline", result.agent_configuration["fixture_transport"])
        self.assertFalse(result.agent_configuration["network_required"])
        self.assertEqual("none", result.agent_configuration["provider_auth"])
        self.assertEqual("gvisor", runtime_metadata["backend"])
        self.assertEqual("none", runtime_metadata["network"])
        self.assertFalse(runtime_metadata["identity_material_present"])
        self.assertTrue(runtime_metadata["isolation_probe"]["passed"])
        self.assertTrue(runtime_metadata["identity_probe"]["passed"])
        self.assertTrue(runtime_metadata["teardown"]["container_removed"])
        self.assertEqual("O1", result.outcome.value)
        self.assertEqual("valid", result.validity.value)


if __name__ == "__main__":
    unittest.main()
