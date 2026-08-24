from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from escape_lab.agent_sandbox import (
    AGENT_RPC_PROTOCOL,
    MAX_AGENT_RESPONSE_BACKLOG,
    GVisorAgentConfig,
    GVisorAgentFactory,
    GVisorAgentSession,
    GVisorFixtureAgentFactory,
    GVisorFixtureAgentSession,
    agent_runtime_digest,
    container_contract_probe,
    gvisor_agent_command,
)
from escape_lab.agent_worker import agent_isolation_probe, identity_probe
from escape_lab.agents import AgentCompleted
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AgentSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ScenarioRegistry(
            PROJECT_ROOT / "scenarios" / "catalog.json"
        )

    def test_command_creates_stopped_gvisor_container_without_identity(
        self,
    ) -> None:
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
        self.assertEqual("create", command[1])
        self.assertIn("--runtime runsc", rendered)
        self.assertIn("--network none", rendered)
        self.assertIn("--ipc none", rendered)
        self.assertIn("--cgroupns private", rendered)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop ALL", rendered)
        self.assertIn("no-new-privileges:true", command)
        self.assertIn("--pull never", rendered)
        self.assertIn("--no-healthcheck", command)
        self.assertIn("--log-driver none", rendered)
        self.assertIn("--memory-swap 512m", rendered)
        self.assertIn("--entrypoint python", rendered)
        self.assertNotIn("--rm", command)
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

    def test_generic_command_uses_image_entrypoint_without_a_shell(
        self,
    ) -> None:
        image_id = "sha256:" + "b" * 64
        command = gvisor_agent_command(
            docker_binary="docker",
            image_reference=image_id,
            container_name="escape-lab-agent-external",
            runtime="runsc",
            memory="512m",
            cpus="1.0",
            pids_limit=64,
            workspace_tmpfs="64m",
            temp_tmpfs="64m",
            run_id="external-run",
            agent_command=None,
        )

        self.assertNotIn("--entrypoint", command)
        self.assertEqual(image_id, command[-1])
        self.assertNotIn("sh", command)
        self.assertNotIn("bash", command)

    def test_config_rejects_non_gvisor_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "runsc"):
            GVisorAgentConfig(
                image="escape-lab-agent:test",
                runtime="runc",
            )

    def test_external_factory_preserves_shell_free_argv(self) -> None:
        config = GVisorAgentConfig(
            image="external-agent:test",
            command=("python", "-m", "agent_worker"),
        )
        factory = GVisorAgentFactory(config)

        self.assertEqual(
            ("python", "-m", "agent_worker"),
            factory.config.command,
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
        image_id = "sha256:" + "a" * 64
        payload = {
            "Image": image_id,
            "State": {
                "Status": "created",
                "Running": False,
                "Pid": 0,
            },
            "AppArmorProfile": "",
            "HostConfig": {
                "Runtime": "runsc",
                "NetworkMode": "none",
                "IpcMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "CapAdd": [],
                "SecurityOpt": ["no-new-privileges:true"],
                "Privileged": False,
                "PidMode": "",
                "UTSMode": "",
                "CgroupnsMode": "private",
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
                "MemorySwap": 536_870_912,
                "NanoCpus": 1_000_000_000,
                "PidsLimit": 64,
                "Ulimits": [
                    {"Name": "nofile", "Soft": 256, "Hard": 256}
                ],
                "RestartPolicy": {
                    "Name": "no",
                    "MaximumRetryCount": 0,
                },
                "AutoRemove": False,
                "LogConfig": {"Type": "none", "Config": {}},
            },
            "Config": {
                "User": "65532:65532",
                "WorkingDir": "/range",
                "Env": ["HOME=/range", "GPG_KEY=public-fingerprint"],
                "Volumes": None,
                "ExposedPorts": {"8080/tcp": {}},
                "Labels": {
                    "io.ostiari.escape-lab.agent-protocol": (
                        AGENT_RPC_PROTOCOL
                    ),
                    "io.ostiari.escape-lab.agent-run-id": "test-run",
                },
                "Entrypoint": ["python"],
                "Cmd": ["-m", "escape_lab.agent_worker"],
                "Healthcheck": {"Test": ["NONE"]},
            },
            "Mounts": [],
            "NetworkSettings": {"Networks": {"none": {}}},
        }

        contract = container_contract_probe(
            payload,
            config,
            expected_image_id=image_id,
            expected_run_id="test-run",
            expected_command=(
                "python",
                "-m",
                "escape_lab.agent_worker",
            ),
        )
        self.assertTrue(contract["passed"])
        self.assertTrue(contract["checks"]["verified_before_execution"])
        self.assertTrue(contract["enforcement"]["syscalls"]["passed"])
        self.assertTrue(contract["enforcement"]["filesystem"]["passed"])
        self.assertTrue(contract["enforcement"]["egress"]["passed"])
        self.assertEqual(["8080/tcp"], contract["declared_ports"])

        payload["Config"]["Env"].append("OPENAI_API_KEY=must-not-enter")
        rejected = container_contract_probe(payload, config)
        self.assertFalse(rejected["passed"])
        self.assertFalse(
            rejected["checks"]["identity_environment_absent"]
        )

    def test_policy_is_verified_before_agent_start(self) -> None:
        config = GVisorAgentConfig(image="external-agent:test")
        rejected_contract = {
            "passed": False,
            "checks": {"verified_before_execution": False},
        }
        with (
            patch.object(
                GVisorAgentSession,
                "_resolve_docker_binary",
                return_value="/bin/echo",
            ),
            patch.object(
                GVisorAgentSession,
                "_docker_server_version",
                return_value="29.2.0",
            ),
            patch.object(
                GVisorAgentSession,
                "_docker_runtimes",
                return_value={"runsc"},
            ),
            patch.object(
                GVisorAgentSession,
                "_inspect_image_id",
                return_value="sha256:" + "a" * 64,
            ),
            patch.object(GVisorAgentSession, "_create_container"),
            patch.object(
                GVisorAgentSession,
                "_inspect_container_contract",
                return_value=rejected_contract,
            ),
            patch.object(GVisorAgentSession, "_force_remove"),
            patch("escape_lab.agent_sandbox.subprocess.Popen") as popen,
            patch("escape_lab.agent_sandbox.queue.Queue") as response_queue,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "pre-execution Docker contract",
            ):
                GVisorAgentSession(
                    self.registry.get("S06"),
                    seed=1,
                    run_id="test-run",
                    config=config,
                )

        popen.assert_not_called()
        response_queue.assert_called_once_with(
            maxsize=MAX_AGENT_RESPONSE_BACKLOG
        )

    def test_host_enforces_agent_turn_limit(self) -> None:
        session = object.__new__(GVisorAgentSession)
        session.config = GVisorAgentConfig(
            image="external-agent:test",
            max_turns=1,
        )
        session._turns = 1

        event = session.next_event()

        self.assertIsInstance(event, AgentCompleted)
        self.assertEqual("maximum agent turns reached", event.reason)

    def test_uncreated_session_never_removes_by_container_name(self) -> None:
        session = object.__new__(GVisorAgentSession)
        session._container_id = None
        session._docker = "docker"
        with patch("escape_lab.agent_sandbox.subprocess.run") as run:
            removed = session._remove_container(force=True)

        self.assertTrue(removed)
        run.assert_not_called()

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

    @unittest.skipUnless(
        os.environ.get("ESCAPE_LAB_EXTERNAL_AGENT_IMAGE"),
        "Set ESCAPE_LAB_EXTERNAL_AGENT_IMAGE to run the external-agent integration",
    )
    def test_real_external_agent_runs_behind_pre_attested_policy(self) -> None:
        image = os.environ["ESCAPE_LAB_EXTERNAL_AGENT_IMAGE"]
        runtime = os.environ.get("ESCAPE_LAB_AGENT_RUNTIME", "runsc")
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=self.registry,
                artifacts_root=Path(temporary),
                agent_factory=GVisorAgentFactory(
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
        self.assertEqual(
            "external-agent-rpc",
            result.agent_configuration["adapter"],
        )
        self.assertEqual(
            "example-agent-rpc",
            result.agent_configuration["reported_agent"]["name"],
        )
        self.assertTrue(runtime_metadata["verified_before_start"])
        self.assertEqual("gvisor-sentry", runtime_metadata["syscall_boundary"])
        self.assertTrue(runtime_metadata["enforcement"]["syscalls"]["passed"])
        self.assertTrue(runtime_metadata["enforcement"]["filesystem"]["passed"])
        self.assertTrue(runtime_metadata["enforcement"]["egress"]["passed"])
        self.assertEqual("none", runtime_metadata["network"])
        self.assertTrue(runtime_metadata["teardown"]["container_removed"])
        self.assertEqual("O1", result.outcome.value)
        self.assertEqual("valid", result.validity.value)


if __name__ == "__main__":
    unittest.main()
