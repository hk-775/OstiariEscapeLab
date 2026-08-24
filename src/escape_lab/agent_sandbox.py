"""Pre-attested gVisor runtime for bounded external Agent-RPC processes."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from escape_lab.agent_protocol import (
    IDENTITY_ENVIRONMENT_NAMES,
    MAX_AGENT_RPC_BYTES,
    decode_event,
)
from escape_lab.agents import (
    AgentAction,
    AgentAdapterError,
    AgentCompleted,
    AgentEvent,
    AgentObservation,
)
from escape_lab.models import Scenario
from escape_lab.util import sha256_json, to_primitive

AGENT_RPC_PROTOCOL = "ostiari-agent-rpc-v1"
FIXTURE_AGENT_COMMAND = ("python", "-m", "escape_lab.agent_worker")
MAX_AGENT_DIAGNOSTIC_BYTES = 4096
MAX_AGENT_RESPONSE_BACKLOG = 4


@dataclass(frozen=True)
class GVisorAgentConfig:
    """Configuration for a zero-egress Agent-RPC process under gVisor."""

    image: str
    command: tuple[str, ...] | None = None
    runtime: str = "runsc"
    docker_binary: str = "docker"
    memory: str = "512m"
    cpus: str = "1.0"
    pids_limit: int = 64
    workspace_tmpfs: str = "64m"
    temp_tmpfs: str = "64m"
    max_turns: int = 20
    startup_timeout_seconds: float = 30.0
    rpc_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if not self.image.strip():
            raise ValueError("A gVisor agent image is required")
        if not self.runtime.startswith("runsc"):
            raise ValueError("The agent runtime must be a runsc runtime")
        if self.command is not None:
            if not isinstance(self.command, tuple) or not self.command:
                raise ValueError("Agent command cannot be empty")
            if any(
                not isinstance(argument, str)
                or not argument
                or "\x00" in argument
                for argument in self.command
            ):
                raise ValueError(
                    "Agent command must contain non-empty strings without NUL bytes"
                )
        if self.pids_limit < 8:
            raise ValueError("Agent pids_limit must be at least 8")
        if self.max_turns < 1:
            raise ValueError("Agent max_turns must be at least 1")
        if self.startup_timeout_seconds <= 0 or self.rpc_timeout_seconds <= 0:
            raise ValueError("Agent sandbox timeouts must be positive")
        _memory_limit_bytes(self.memory)
        _nano_cpus(self.cpus)


class GVisorAgentFactory:
    """Create an arbitrary Agent-RPC image inside a pre-attested gVisor boundary."""

    def __init__(self, config: GVisorAgentConfig) -> None:
        self.config = config

    def start(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
    ) -> GVisorAgentSession:
        return GVisorAgentSession(
            scenario,
            seed=seed,
            run_id=run_id,
            config=self.config,
        )


class GVisorFixtureAgentFactory:
    """Create an offline AxonLLM fixture agent inside gVisor."""

    def __init__(self, config: GVisorAgentConfig) -> None:
        self.config = config

    def start(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
    ) -> GVisorFixtureAgentSession:
        if self.config.command not in {None, FIXTURE_AGENT_COMMAND}:
            raise AgentAdapterError(
                "The reviewed AxonLLM fixture uses a fixed worker command"
            )
        return GVisorFixtureAgentSession(
            scenario,
            seed=seed,
            run_id=run_id,
            config=self.config,
        )


def gvisor_agent_command(
    *,
    docker_binary: str,
    image_reference: str,
    container_name: str,
    runtime: str,
    memory: str,
    cpus: str,
    pids_limit: int,
    workspace_tmpfs: str,
    temp_tmpfs: str,
    run_id: str,
    agent_command: tuple[str, ...] | None = FIXTURE_AGENT_COMMAND,
) -> list[str]:
    """Build a shell-free Docker create command for pre-execution attestation."""

    command = [
        docker_binary,
        "create",
        "--interactive",
        "--pull",
        "never",
        "--name",
        container_name,
        "--hostname",
        "escape-lab-agent",
        "--label",
        f"io.ostiari.escape-lab.agent-run-id={run_id}",
        "--label",
        f"io.ostiari.escape-lab.agent-protocol={AGENT_RPC_PROTOCOL}",
        "--runtime",
        runtime,
        "--network",
        "none",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--restart",
        "no",
        "--no-healthcheck",
        "--log-driver",
        "none",
        "--pids-limit",
        str(pids_limit),
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        cpus,
        "--ulimit",
        "nofile=256:256",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={temp_tmpfs},mode=1777",
        "--tmpfs",
        (
            f"/range:rw,noexec,nosuid,nodev,size={workspace_tmpfs},"
            "mode=0700,uid=65532,gid=65532"
        ),
        "--user",
        "65532:65532",
        "--workdir",
        "/range",
        "--env",
        "HOME=/range",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONNOUSERSITE=1",
        "--env",
        "PYTHONUNBUFFERED=1",
    ]
    if agent_command is not None:
        command.extend(["--entrypoint", agent_command[0]])
    command.append(image_reference)
    if agent_command is not None:
        command.extend(agent_command[1:])
    return command


def agent_runtime_digest(metadata: dict[str, Any]) -> str:
    """Hash stable agent-boundary identity while excluding container names."""

    probe = metadata.get("isolation_probe", {})
    checks = probe.get("checks", {}) if isinstance(probe, dict) else {}
    contract = metadata.get("container_contract", {})
    contract_checks = (
        contract.get("checks", {}) if isinstance(contract, dict) else {}
    )
    return sha256_json(
        {
            "backend": metadata.get("backend"),
            "runtime": metadata.get("runtime"),
            "docker_server_version": metadata.get("docker_server_version"),
            "image_id": metadata.get("image_id"),
            "network": metadata.get("network"),
            "provider_transport": metadata.get("provider_transport"),
            "provider_auth": metadata.get("provider_auth"),
            "protocol": metadata.get("protocol"),
            "process_command": metadata.get("process_command"),
            "enforcement": metadata.get("enforcement"),
            "control_channel": metadata.get("control_channel"),
            "user": metadata.get("user"),
            "capabilities": metadata.get("capabilities"),
            "root_filesystem": metadata.get("root_filesystem"),
            "limits": metadata.get("limits"),
            "isolation_probe_checks": checks,
            "container_contract_checks": contract_checks,
        }
    )


def container_contract_probe(
    payload: dict[str, Any],
    config: GVisorAgentConfig,
    *,
    expected_image_id: str | None = None,
    expected_run_id: str | None = None,
    expected_command: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Verify Docker's effective policy while the container is still stopped."""

    host = payload.get("HostConfig", {})
    container = payload.get("Config", {})
    network_settings = payload.get("NetworkSettings", {})
    state = payload.get("State", {})
    if not isinstance(host, dict):
        host = {}
    if not isinstance(container, dict):
        container = {}
    if not isinstance(network_settings, dict):
        network_settings = {}
    if not isinstance(state, dict):
        state = {}

    labels = container.get("Labels", {})
    if not isinstance(labels, dict):
        labels = {}
    healthcheck = container.get("Healthcheck")
    if not isinstance(healthcheck, dict):
        healthcheck = {}
    healthcheck_test = healthcheck.get("Test", [])
    if not isinstance(healthcheck_test, list):
        healthcheck_test = []
    entrypoint = container.get("Entrypoint")
    if not isinstance(entrypoint, list):
        entrypoint = []
    configured_command = container.get("Cmd")
    if not isinstance(configured_command, list):
        configured_command = []
    effective_command = tuple(
        str(argument) for argument in [*entrypoint, *configured_command]
    )
    if expected_command is None:
        process_command_matches = bool(effective_command)
    else:
        process_command_matches = (
            [str(argument) for argument in entrypoint]
            == [expected_command[0]]
            and [str(argument) for argument in configured_command]
            == list(expected_command[1:])
        )

    environments = container.get("Env", [])
    if not isinstance(environments, list):
        environments = []
    environment_names = sorted(
        str(entry).partition("=")[0] for entry in environments
    )
    identity_environment_names = sorted(
        name for name in environment_names if _identity_environment_name(name)
    )

    tmpfs = host.get("Tmpfs", {})
    if not isinstance(tmpfs, dict):
        tmpfs = {}
    range_tmpfs = _tmpfs_options(tmpfs.get("/range"))
    temp_tmpfs = _tmpfs_options(tmpfs.get("/tmp"))
    required_tmpfs = {"rw", "noexec", "nosuid", "nodev"}

    networks = network_settings.get("Networks", {})
    if not isinstance(networks, dict):
        networks = {}
    security_options = host.get("SecurityOpt", [])
    if not isinstance(security_options, list):
        security_options = []
    normalized_security_options = {
        str(option).lower() for option in security_options
    }
    cap_drop = host.get("CapDrop", [])
    if not isinstance(cap_drop, list):
        cap_drop = []
    cap_add = host.get("CapAdd", [])
    if not isinstance(cap_add, list):
        cap_add = []
    ulimits = host.get("Ulimits", [])
    if not isinstance(ulimits, list):
        ulimits = []
    nofile_limited = any(
        isinstance(limit, dict)
        and limit.get("Name") == "nofile"
        and int(limit.get("Soft", 0)) == 256
        and int(limit.get("Hard", 0)) == 256
        for limit in ulimits
    )
    restart_policy = host.get("RestartPolicy", {})
    if not isinstance(restart_policy, dict):
        restart_policy = {}
    log_config = host.get("LogConfig", {})
    if not isinstance(log_config, dict):
        log_config = {}

    protocol_label = labels.get(
        "io.ostiari.escape-lab.agent-protocol"
    )
    run_id_label = labels.get("io.ostiari.escape-lab.agent-run-id")
    expected_memory = _memory_limit_bytes(config.memory)
    expected_nano_cpus = _nano_cpus(config.cpus)

    checks = {
        "verified_before_execution": (
            state.get("Status") == "created"
            and state.get("Running") is False
            and int(state.get("Pid", 0)) == 0
        ),
        "immutable_image": (
            expected_image_id is None
            or payload.get("Image") == expected_image_id
        ),
        "agent_rpc_protocol": protocol_label == AGENT_RPC_PROTOCOL,
        "run_id_bound": (
            expected_run_id is None or run_id_label == expected_run_id
        ),
        "process_command_present_and_bound": process_command_matches,
        "runsc_runtime": (
            host.get("Runtime") == config.runtime
            and config.runtime.startswith("runsc")
        ),
        "network_none": (
            host.get("NetworkMode") == "none"
            and set(networks).issubset({"none"})
        ),
        "ipc_none": host.get("IpcMode") == "none",
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "non_root": container.get("User") == "65532:65532",
        "workspace_isolated": container.get("WorkingDir") == "/range",
        "capabilities_dropped": "ALL" in {
            str(capability).upper() for capability in cap_drop
        },
        "no_capabilities_added": not cap_add,
        "no_new_privileges": any(
            option in {
                "no-new-privileges",
                "no-new-privileges:true",
            }
            for option in normalized_security_options
        ),
        "seccomp_not_unconfined": "seccomp=unconfined"
        not in normalized_security_options,
        "apparmor_not_unconfined": (
            str(payload.get("AppArmorProfile", "")).lower()
            != "unconfined"
        ),
        "not_privileged": host.get("Privileged") is False,
        "pid_namespace_private": host.get("PidMode") in {"", "private", None},
        "uts_namespace_private": host.get("UTSMode") in {"", "private", None},
        "cgroup_namespace_private": host.get("CgroupnsMode") == "private",
        "no_host_mounts": (
            not host.get("Binds")
            and not host.get("VolumesFrom")
            and not payload.get("Mounts")
            and not container.get("Volumes")
        ),
        "no_devices": (
            not host.get("Devices")
            and not host.get("DeviceRequests")
            and not host.get("DeviceCgroupRules")
        ),
        "no_published_ports": (
            not host.get("PortBindings")
            and host.get("PublishAllPorts") is False
        ),
        "healthcheck_disabled": (
            not healthcheck_test
            or [str(value).upper() for value in healthcheck_test] == ["NONE"]
        ),
        "restart_disabled": (
            restart_policy.get("Name") in {"", "no", None}
            and int(restart_policy.get("MaximumRetryCount", 0)) == 0
        ),
        "container_logging_disabled": log_config.get("Type") == "none",
        "range_tmpfs_hardened": required_tmpfs.issubset(range_tmpfs),
        "temp_tmpfs_hardened": required_tmpfs.issubset(temp_tmpfs),
        "identity_environment_absent": not identity_environment_names,
        "memory_limited": int(host.get("Memory", 0)) == expected_memory,
        "swap_limited": int(host.get("MemorySwap", 0)) == expected_memory,
        "cpu_limited": int(host.get("NanoCpus", 0))
        == expected_nano_cpus,
        "pids_limited": host.get("PidsLimit") == config.pids_limit,
        "files_limited": nofile_limited,
        "explicit_removal": host.get("AutoRemove") is False,
    }
    enforcement = {
        "syscalls": {
            "mechanism": "gvisor-sentry",
            "passed": all(
                checks[name]
                for name in (
                    "runsc_runtime",
                    "capabilities_dropped",
                    "no_capabilities_added",
                    "no_new_privileges",
                    "seccomp_not_unconfined",
                    "not_privileged",
                    "no_devices",
                )
            ),
        },
        "filesystem": {
            "mechanism": "read-only-root-and-hardened-tmpfs",
            "passed": all(
                checks[name]
                for name in (
                    "read_only_root",
                    "workspace_isolated",
                    "no_host_mounts",
                    "range_tmpfs_hardened",
                    "temp_tmpfs_hardened",
                )
            ),
        },
        "egress": {
            "mechanism": "docker-network-none",
            "passed": all(
                checks[name]
                for name in (
                    "network_none",
                    "no_published_ports",
                )
            ),
        },
        "identity": {
            "mechanism": "no-host-injection",
            "passed": all(
                checks[name]
                for name in (
                    "identity_environment_absent",
                    "no_host_mounts",
                    "no_devices",
                )
            ),
        },
        "host_io": {
            "mechanism": "bounded-agent-rpc-without-container-logs",
            "passed": checks["container_logging_disabled"],
        },
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "enforcement": enforcement,
        "verified_phase": state.get("Status"),
        "runtime": host.get("Runtime"),
        "network_mode": host.get("NetworkMode"),
        "ipc_mode": host.get("IpcMode"),
        "user": container.get("User"),
        "image_id": payload.get("Image"),
        "protocol": protocol_label,
        "run_id": run_id_label,
        "process_command": list(effective_command),
        "declared_ports": sorted(
            str(port)
            for port in (
                container.get("ExposedPorts", {})
                if isinstance(container.get("ExposedPorts"), dict)
                else {}
            )
        ),
        "environment_names": environment_names,
        "identity_environment_names": identity_environment_names,
        "tmpfs_options": {
            "/range": sorted(range_tmpfs),
            "/tmp": sorted(temp_tmpfs),
        },
    }


class GVisorAgentSession:
    """Host proxy for an arbitrary Agent-RPC process running under gVisor."""

    requires_verified_teardown = True

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
        config: GVisorAgentConfig,
        fixture_contract: bool = False,
    ) -> None:
        self.config = config
        self._fixture_contract = fixture_contract
        self._agent_command = (
            FIXTURE_AGENT_COMMAND if fixture_contract else config.command
        )
        self._closed = False
        self._request_id = 0
        self._turns = 0
        self._write_lock = threading.Lock()
        self._responses: queue.Queue[bytes | None] = queue.Queue(
            maxsize=MAX_AGENT_RESPONSE_BACKLOG
        )
        self._reader_stop = threading.Event()
        self._stderr: deque[str] = deque(maxlen=50)
        self._process: Any = None
        self._container_id: str | None = None
        self.teardown: dict[str, Any] = {"complete": False}

        self._docker = self._resolve_docker_binary(config.docker_binary)
        self._server_version = self._docker_server_version()
        available_runtimes = self._docker_runtimes()
        if config.runtime not in available_runtimes:
            available = ", ".join(sorted(available_runtimes)) or "none"
            raise AgentAdapterError(
                f"Required gVisor runtime {config.runtime!r} is unavailable; "
                f"configured runtimes: {available}"
            )
        self._image_id = self._inspect_image_id(config.image)
        self._container_name = _agent_container_name(run_id)
        create_command = gvisor_agent_command(
            docker_binary=self._docker,
            image_reference=self._image_id,
            container_name=self._container_name,
            runtime=config.runtime,
            memory=config.memory,
            cpus=config.cpus,
            pids_limit=config.pids_limit,
            workspace_tmpfs=config.workspace_tmpfs,
            temp_tmpfs=config.temp_tmpfs,
            run_id=run_id,
            agent_command=self._agent_command,
        )

        try:
            self._create_container(create_command)
            container_contract = self._inspect_container_contract(run_id)
            if not container_contract.get("passed"):
                raise AgentAdapterError(
                    "gVisor agent violated its pre-execution Docker contract: "
                    f"{container_contract}"
                )
            self._start_container()
            initialized = self._rpc(
                "init",
                {
                    "scenario": scenario.data,
                    "scenario_digest": scenario.digest,
                    "seed": seed,
                    "run_id": run_id,
                    "max_turns": config.max_turns,
                    "protocol": AGENT_RPC_PROTOCOL,
                },
                timeout=config.startup_timeout_seconds,
            )
            agent_metadata, isolation, identity = (
                self._validate_initialization(initialized)
            )
            runtime_metadata = {
                "backend": "gvisor",
                "runtime": config.runtime,
                "docker_server_version": self._server_version,
                "image_requested": config.image,
                "image_id": self._image_id,
                "container_id": self._container_id,
                "container_name": self._container_name,
                "protocol": AGENT_RPC_PROTOCOL,
                "process_command": container_contract.get(
                    "process_command",
                    [],
                ),
                "verified_before_start": True,
                "network": "none",
                "egress": "none",
                "provider_transport": (
                    "in-process"
                    if fixture_contract
                    else "agent-local-only"
                ),
                "provider_auth": (
                    "none" if fixture_contract else "not-injected"
                ),
                "identity_material_present": False,
                "identity_scope": (
                    "host injection denied; image contents require "
                    "separate supply-chain review"
                ),
                "environment_forwarding": "none",
                "ipc": "none",
                "root_filesystem": "read-only",
                "user": "65532:65532",
                "capabilities": "none",
                "no_new_privileges": True,
                "syscall_boundary": "gvisor-sentry",
                "enforcement": deepcopy(
                    container_contract.get("enforcement", {})
                ),
                "control_channel": {
                    "transport": "attached-stdio",
                    "max_record_bytes": MAX_AGENT_RPC_BYTES,
                    "response_backlog": MAX_AGENT_RESPONSE_BACKLOG,
                    "diagnostic_chunk_bytes": MAX_AGENT_DIAGNOSTIC_BYTES,
                    "container_logging": "disabled",
                    "host_enforced_max_turns": config.max_turns,
                },
                "limits": {
                    "memory": config.memory,
                    "memory_swap": config.memory,
                    "cpus": config.cpus,
                    "pids": config.pids_limit,
                    "nofile": "256:256",
                    "workspace_tmpfs": config.workspace_tmpfs,
                    "temp_tmpfs": config.temp_tmpfs,
                },
                "worker_attestation_required": fixture_contract,
                "isolation_probe": isolation,
                "identity_probe": identity,
                "container_contract": container_contract,
            }
            runtime_metadata["digest"] = agent_runtime_digest(
                runtime_metadata
            )
            if fixture_contract:
                self.metadata = {
                    **deepcopy(agent_metadata),
                    "runtime": runtime_metadata,
                }
            else:
                self.metadata = {
                    "adapter": "external-agent-rpc",
                    "version": "1.0.0",
                    "mode": "isolated",
                    "protocol": AGENT_RPC_PROTOCOL,
                    "network_required": False,
                    "provider_auth": "not-injected",
                    "reported_agent": deepcopy(agent_metadata),
                    "runtime": runtime_metadata,
                }
        except BaseException:
            self._force_remove()
            raise

    def next_event(self) -> AgentEvent:
        if self._turns >= self.config.max_turns:
            return AgentCompleted(reason="maximum agent turns reached")
        self._turns += 1
        return decode_event(self._rpc("next_event"))

    def observe(
        self,
        action: AgentAction,
        observation: AgentObservation,
    ) -> None:
        self._rpc(
            "observe",
            {
                "action": to_primitive(action),
                "observation": to_primitive(observation),
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        error: BaseException | None = None
        forced_removal = False
        try:
            self._rpc("shutdown")
            self._process.wait(timeout=5)
            self._close_process_streams()
        except BaseException as caught:
            error = caught
            forced_removal = True
            self._force_remove()

        removed = not self._container_exists()
        if not removed and error is None:
            removed = self._remove_container(force=False)
        if not removed:
            forced_removal = True
            self._force_remove()
            removed = not self._container_exists()

        self._closed = True
        self.teardown = {
            "complete": removed and error is None,
            "container_removed": removed,
            "forced_removal": forced_removal,
            "runtime": self.config.runtime,
            "image_id": self._image_id,
            "container_id": self._container_id,
        }
        self.metadata["runtime"]["teardown"] = deepcopy(self.teardown)
        if error is not None:
            raise AgentAdapterError(
                f"gVisor agent teardown failed: {error}"
            ) from error
        if not removed:
            raise AgentAdapterError(
                "gVisor agent container remained after teardown"
            )

    def _validate_initialization(
        self,
        initialized: Any,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not isinstance(initialized, dict):
            raise AgentAdapterError(
                "gVisor agent initialization was not an object"
            )
        agent_metadata = initialized.get("agent_metadata")
        if not isinstance(agent_metadata, dict):
            raise AgentAdapterError(
                "gVisor agent metadata was not an object"
            )
        isolation = self._worker_probe(
            "isolation",
            initialized.get("isolation_probe"),
        )
        identity = self._worker_probe(
            "identity",
            initialized.get("identity_probe"),
        )
        if self._fixture_contract:
            expected = {
                "adapter": "axonllm",
                "mode": "fixture",
                "fixture_transport": "offline",
                "network_required": False,
                "provider_auth": "none",
            }
            mismatched = {
                key: agent_metadata.get(key)
                for key, value in expected.items()
                if agent_metadata.get(key) != value
            }
            if mismatched:
                raise AgentAdapterError(
                    "gVisor fixture agent violated its offline contract: "
                    f"{mismatched}"
                )
        return agent_metadata, isolation, identity

    def _worker_probe(self, name: str, value: Any) -> dict[str, Any]:
        if value is None and not self._fixture_contract:
            return {
                "reported": False,
                "passed": None,
                "trust": "host contract is authoritative",
            }
        if not isinstance(value, dict) or value.get("passed") is not True:
            raise AgentAdapterError(
                f"gVisor agent failed its {name} probe: {value}"
            )
        return deepcopy(value)

    def _rpc(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self._closed:
            raise AgentAdapterError(
                "gVisor agent session has already closed"
            )
        if self._process is None or self._process.stdin is None:
            raise AgentAdapterError(
                "gVisor agent control channel is unavailable"
            )
        self._request_id += 1
        request_id = self._request_id
        encoded = (
            json.dumps(
                {
                    "id": request_id,
                    "operation": operation,
                    "payload": payload or {},
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_AGENT_RPC_BYTES:
            self._force_remove()
            raise AgentAdapterError(
                "gVisor agent RPC request exceeded the size limit"
            )
        try:
            with self._write_lock:
                self._process.stdin.write(encoded)
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AgentAdapterError(
                "gVisor agent control channel failed: "
                f"{self._diagnostic()}"
            ) from error

        wait_seconds = timeout or self.config.rpc_timeout_seconds
        try:
            line = self._responses.get(timeout=wait_seconds)
        except queue.Empty as error:
            self._force_remove()
            raise AgentAdapterError(
                f"gVisor agent timed out during {operation!r}"
            ) from error
        if line is None:
            raise AgentAdapterError(
                "gVisor agent exited during "
                f"{operation!r}: {self._diagnostic()}"
            )
        if len(line) > MAX_AGENT_RPC_BYTES:
            self._force_remove()
            raise AgentAdapterError(
                "gVisor agent RPC response exceeded the size limit"
            )
        try:
            response = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentAdapterError(
                "gVisor agent emitted invalid JSON"
            ) from error
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise AgentAdapterError(
                "gVisor agent response correlation failed"
            )
        if not response.get("ok"):
            detail = response.get("error", {})
            if not isinstance(detail, dict):
                detail = {}
            raise AgentAdapterError(
                f"gVisor agent rejected {operation!r}: "
                f"{detail.get('type', 'error')}: "
                f"{detail.get('message', '')}"
            )
        return response.get("result")

    def _create_container(self, command: list[str]) -> None:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=self.config.startup_timeout_seconds,
        )
        container_id = process.stdout.strip()
        if process.returncode != 0 or not re.fullmatch(
            r"[0-9a-f]{64}",
            container_id,
        ):
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(
                f"Could not create gVisor agent container: {detail}"
            )
        self._container_id = container_id

    def _start_container(self) -> None:
        if self._container_id is None:
            raise AgentAdapterError(
                "gVisor agent container was not created"
            )
        try:
            self._process = subprocess.Popen(
                [
                    self._docker,
                    "start",
                    "--attach",
                    "--interactive",
                    self._container_id,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise AgentAdapterError(
                f"Could not start gVisor agent: {error}"
            ) from error
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            raise AgentAdapterError(
                "gVisor agent did not expose its control pipes"
            )
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name=f"{self._container_name}-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"{self._container_name}-stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self._process is not None
        assert self._process.stdout is not None
        while not self._reader_stop.is_set():
            line = self._process.stdout.readline(MAX_AGENT_RPC_BYTES + 1)
            if not line:
                break
            if not self._enqueue_response(line):
                return
            if len(line) > MAX_AGENT_RPC_BYTES:
                break
        self._enqueue_response(None)

    def _read_stderr(self) -> None:
        assert self._process is not None
        assert self._process.stderr is not None
        while not self._reader_stop.is_set():
            line = self._process.stderr.readline(
                MAX_AGENT_DIAGNOSTIC_BYTES + 1
            )
            if not line:
                break
            truncated = len(line) > MAX_AGENT_DIAGNOSTIC_BYTES
            self._stderr.append(
                line[:MAX_AGENT_DIAGNOSTIC_BYTES]
                .decode("utf-8", errors="replace")
                .rstrip()
                + (" [truncated]" if truncated else "")
            )

    def _enqueue_response(self, value: bytes | None) -> bool:
        while not self._reader_stop.is_set():
            try:
                self._responses.put(value, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _force_remove(self) -> None:
        self._reader_stop.set()
        self._remove_container(force=True)
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._close_process_streams()

    def _remove_container(self, *, force: bool) -> bool:
        if self._container_id is None:
            return True
        command = [self._docker, "rm"]
        if force:
            command.append("--force")
        command.append(self._container_id)
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=10,
        )
        return process.returncode == 0 or not self._container_exists()

    def _close_process_streams(self) -> None:
        process = self._process
        if process is None:
            return
        for thread_name in ("_stdout_thread", "_stderr_thread"):
            thread = getattr(self, thread_name, None)
            if thread is not None and thread.is_alive():
                thread.join(timeout=1)
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None and not stream.closed:
                stream.close()

    def _container_exists(self) -> bool:
        if self._container_id is None:
            return False
        process = subprocess.run(
            [self._docker, "inspect", self._container_id],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return process.returncode == 0

    def _inspect_container_contract(self, run_id: str) -> dict[str, Any]:
        if self._container_id is None:
            raise AgentAdapterError(
                "gVisor agent container was not created"
            )
        process = subprocess.run(
            [self._docker, "inspect", self._container_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(
                "Could not inspect the stopped gVisor agent: "
                f"{detail}"
            )
        try:
            records = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise AgentAdapterError(
                "Docker returned invalid agent-container metadata"
            ) from error
        if (
            not isinstance(records, list)
            or len(records) != 1
            or not isinstance(records[0], dict)
        ):
            raise AgentAdapterError(
                "Docker returned an invalid agent-container record"
            )
        return container_contract_probe(
            records[0],
            self.config,
            expected_image_id=self._image_id,
            expected_run_id=run_id,
            expected_command=self._agent_command,
        )

    def _docker_server_version(self) -> str:
        process = subprocess.run(
            [self._docker, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0 or not process.stdout.strip():
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(f"Docker daemon is unavailable: {detail}")
        return process.stdout.strip()

    def _docker_runtimes(self) -> set[str]:
        process = subprocess.run(
            [self._docker, "info", "--format", "{{json .Runtimes}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(
                f"Could not inspect Docker runtimes: {detail}"
            )
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise AgentAdapterError(
                "Docker returned invalid runtime metadata"
            ) from error
        return set(payload) if isinstance(payload, dict) else set()

    def _inspect_image_id(self, image: str) -> str:
        process = subprocess.run(
            [
                self._docker,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                image,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        image_id = process.stdout.strip()
        if process.returncode != 0 or not image_id.startswith("sha256:"):
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(
                f"Agent image {image!r} is unavailable or unpinned: "
                f"{detail}"
            )
        return image_id

    def _diagnostic(self) -> str:
        detail = " | ".join(self._stderr)
        if detail:
            return detail
        if self._process is None:
            return "container-not-started"
        return f"exit={self._process.poll()}"

    @staticmethod
    def _resolve_docker_binary(value: str) -> str:
        if "/" in value:
            path = Path(value)
            if path.is_file():
                return str(path)
            raise AgentAdapterError(
                f"Docker executable does not exist: {value}"
            )
        resolved = shutil.which(value)
        if resolved is None:
            raise AgentAdapterError(
                f"Docker executable was not found: {value}"
            )
        return resolved


class GVisorFixtureAgentSession(GVisorAgentSession):
    """Backward-compatible reviewed AxonLLM fixture contract."""

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
        config: GVisorAgentConfig,
    ) -> None:
        super().__init__(
            scenario,
            seed=seed,
            run_id=run_id,
            config=config,
            fixture_contract=True,
        )


def _agent_container_name(run_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-.")
    return f"escape-lab-agent-{normalized}"[:120]


def _tmpfs_options(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {option for option in value.split(",") if option}


def _memory_limit_bytes(value: str) -> int:
    normalized = value.strip().lower()
    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*(b|k|kb|kib|m|mb|mib|g|gb|gib)?",
        normalized,
    )
    if match is None:
        raise ValueError(f"Invalid agent memory limit: {value!r}")
    multipliers = {
        None: 1,
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
    }
    result = int(Decimal(match.group(1)) * multipliers[match.group(2)])
    if result <= 0:
        raise ValueError("Agent memory limit must be positive")
    return result


def _nano_cpus(value: str) -> int:
    try:
        result = int(Decimal(value.strip()) * Decimal(1_000_000_000))
    except (InvalidOperation, AttributeError) as error:
        raise ValueError(f"Invalid agent CPU limit: {value!r}") from error
    if result <= 0:
        raise ValueError("Agent CPU limit must be positive")
    return result


def _identity_environment_name(name: str) -> bool:
    upper = name.upper()
    return (
        upper in IDENTITY_ENVIRONMENT_NAMES
        or upper in {"API_KEY", "PASSWORD", "SECRET", "TOKEN"}
        or upper.endswith(
            (
                "_ACCESS_TOKEN",
                "_API_KEY",
                "_AUTH_TOKEN",
                "_CLIENT_SECRET",
                "_CREDENTIALS",
                "_SECRET_ACCESS_KEY",
                "_SECRET_KEY",
            )
        )
    )
