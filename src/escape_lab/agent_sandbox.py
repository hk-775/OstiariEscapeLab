"""gVisor-backed fixture-agent runtime with no network or identity material."""

from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
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
    AgentEvent,
    AgentObservation,
)
from escape_lab.models import Scenario
from escape_lab.util import sha256_json, to_primitive


@dataclass(frozen=True)
class GVisorAgentConfig:
    """Configuration for the offline AxonLLM fixture-agent boundary."""

    image: str
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
            raise ValueError("A gVisor fixture-agent image is required")
        if not self.runtime.startswith("runsc"):
            raise ValueError("The fixture-agent runtime must be a runsc runtime")
        if self.pids_limit < 8:
            raise ValueError("Agent pids_limit must be at least 8")
        if self.max_turns < 1:
            raise ValueError("Agent max_turns must be at least 1")
        if self.startup_timeout_seconds <= 0 or self.rpc_timeout_seconds <= 0:
            raise ValueError("Agent sandbox timeouts must be positive")


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
) -> list[str]:
    """Build the shell-free, credential-free fixture-agent command."""

    return [
        docker_binary,
        "run",
        "--rm",
        "--interactive",
        "--pull",
        "never",
        "--name",
        container_name,
        "--hostname",
        "escape-lab-agent",
        "--label",
        f"io.ostiari.escape-lab.agent-run-id={run_id}",
        "--runtime",
        runtime,
        "--network",
        "none",
        "--ipc",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(pids_limit),
        "--memory",
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
        "--entrypoint",
        "python",
        image_reference,
        "-m",
        "escape_lab.agent_worker",
    ]


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
) -> dict[str, Any]:
    """Verify the actual Docker container record for the agent boundary."""

    host = payload.get("HostConfig", {})
    container = payload.get("Config", {})
    network_settings = payload.get("NetworkSettings", {})
    if not isinstance(host, dict):
        host = {}
    if not isinstance(container, dict):
        container = {}
    if not isinstance(network_settings, dict):
        network_settings = {}

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
    cap_drop = host.get("CapDrop", [])
    if not isinstance(cap_drop, list):
        cap_drop = []
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

    checks = {
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
        "capabilities_dropped": "ALL" in {
            str(capability).upper() for capability in cap_drop
        },
        "no_new_privileges": "no-new-privileges:true" in security_options,
        "not_privileged": host.get("Privileged") is False,
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
            and not container.get("ExposedPorts")
            and host.get("PublishAllPorts") is False
        ),
        "range_tmpfs_hardened": required_tmpfs.issubset(range_tmpfs),
        "temp_tmpfs_hardened": required_tmpfs.issubset(temp_tmpfs),
        "identity_environment_absent": not identity_environment_names,
        "memory_limited": int(host.get("Memory", 0)) > 0,
        "cpu_limited": int(host.get("NanoCpus", 0)) > 0,
        "pids_limited": host.get("PidsLimit") == config.pids_limit,
        "files_limited": nofile_limited,
        "automatic_removal": host.get("AutoRemove") is True,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "runtime": host.get("Runtime"),
        "network_mode": host.get("NetworkMode"),
        "ipc_mode": host.get("IpcMode"),
        "user": container.get("User"),
        "environment_names": environment_names,
        "identity_environment_names": identity_environment_names,
        "tmpfs_options": {
            "/range": sorted(range_tmpfs),
            "/tmp": sorted(temp_tmpfs),
        },
    }


class GVisorFixtureAgentSession:
    """Host proxy for an AxonLLM fixture loop running entirely in gVisor."""

    requires_verified_teardown = True

    def __init__(
        self,
        scenario: Scenario,
        *,
        seed: int,
        run_id: str,
        config: GVisorAgentConfig,
    ) -> None:
        self.config = config
        self._closed = False
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._responses: queue.Queue[bytes | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=50)
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
        command = gvisor_agent_command(
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
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as error:
            raise AgentAdapterError(
                f"Could not start gVisor fixture agent: {error}"
            ) from error
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            self._force_remove()
            raise AgentAdapterError(
                "gVisor fixture agent did not expose its control pipes"
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

        try:
            container_contract = self._wait_for_container_contract(
                timeout=config.startup_timeout_seconds,
            )
            if not container_contract.get("passed"):
                raise AgentAdapterError(
                    "gVisor fixture agent violated its Docker contract: "
                    f"{container_contract}"
                )
            initialized = self._rpc(
                "init",
                {
                    "scenario": scenario.data,
                    "scenario_digest": scenario.digest,
                    "seed": seed,
                    "run_id": run_id,
                    "max_turns": config.max_turns,
                },
                timeout=config.startup_timeout_seconds,
            )
            if not isinstance(initialized, dict):
                raise AgentAdapterError(
                    "gVisor fixture agent initialization was not an object"
                )
            isolation = initialized.get("isolation_probe")
            identity = initialized.get("identity_probe")
            agent_metadata = initialized.get("agent_metadata")
            if not isinstance(isolation, dict) or not isolation.get("passed"):
                raise AgentAdapterError(
                    "gVisor fixture agent failed its isolation probe: "
                    f"{isolation}"
                )
            if not isinstance(identity, dict) or not identity.get("passed"):
                raise AgentAdapterError(
                    "gVisor fixture agent received identity material: "
                    f"{identity}"
                )
            if not isinstance(agent_metadata, dict):
                raise AgentAdapterError(
                    "gVisor fixture agent metadata was not an object"
                )
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
            runtime_metadata = {
                "backend": "gvisor",
                "runtime": config.runtime,
                "docker_server_version": self._server_version,
                "image_requested": config.image,
                "image_id": self._image_id,
                "container_name": self._container_name,
                "network": "none",
                "provider_transport": "in-process",
                "provider_auth": "none",
                "identity_material_present": False,
                "environment_forwarding": "none",
                "ipc": "none",
                "root_filesystem": "read-only",
                "user": "65532:65532",
                "capabilities": "none",
                "no_new_privileges": True,
                "limits": {
                    "memory": config.memory,
                    "cpus": config.cpus,
                    "pids": config.pids_limit,
                    "nofile": "256:256",
                    "workspace_tmpfs": config.workspace_tmpfs,
                    "temp_tmpfs": config.temp_tmpfs,
                },
                "isolation_probe": isolation,
                "identity_probe": identity,
                "container_contract": container_contract,
            }
            runtime_metadata["digest"] = agent_runtime_digest(
                runtime_metadata
            )
            self.metadata = {
                **deepcopy(agent_metadata),
                "runtime": runtime_metadata,
            }
        except BaseException:
            self._force_remove()
            raise

    def next_event(self) -> AgentEvent:
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
        try:
            self._rpc("shutdown")
            self._process.wait(timeout=5)
            self._close_process_streams()
        except BaseException as caught:
            error = caught
            self._force_remove()
        removed = not self._container_exists()
        if not removed:
            self._force_remove()
            removed = not self._container_exists()
        self._closed = True
        self.teardown = {
            "complete": removed and error is None,
            "container_removed": removed,
            "runtime": self.config.runtime,
            "image_id": self._image_id,
        }
        self.metadata["runtime"]["teardown"] = deepcopy(self.teardown)
        if error is not None:
            raise AgentAdapterError(
                f"gVisor fixture-agent teardown failed: {error}"
            ) from error
        if not removed:
            raise AgentAdapterError(
                "gVisor fixture-agent container remained after teardown"
            )

    def _rpc(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self._closed:
            raise AgentAdapterError(
                "gVisor fixture-agent session has already closed"
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
                "gVisor fixture-agent RPC request exceeded the size limit"
            )
        assert self._process.stdin is not None
        try:
            with self._write_lock:
                self._process.stdin.write(encoded)
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AgentAdapterError(
                "gVisor fixture-agent control channel failed: "
                f"{self._diagnostic()}"
            ) from error

        wait_seconds = timeout or self.config.rpc_timeout_seconds
        try:
            line = self._responses.get(timeout=wait_seconds)
        except queue.Empty as error:
            self._force_remove()
            raise AgentAdapterError(
                f"gVisor fixture agent timed out during {operation!r}"
            ) from error
        if line is None:
            raise AgentAdapterError(
                "gVisor fixture agent exited during "
                f"{operation!r}: {self._diagnostic()}"
            )
        if len(line) > MAX_AGENT_RPC_BYTES:
            self._force_remove()
            raise AgentAdapterError(
                "gVisor fixture-agent RPC response exceeded the size limit"
            )
        try:
            response = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AgentAdapterError(
                "gVisor fixture agent emitted invalid JSON"
            ) from error
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise AgentAdapterError(
                "gVisor fixture-agent response correlation failed"
            )
        if not response.get("ok"):
            detail = response.get("error", {})
            if not isinstance(detail, dict):
                detail = {}
            raise AgentAdapterError(
                f"gVisor fixture agent rejected {operation!r}: "
                f"{detail.get('type', 'error')}: "
                f"{detail.get('message', '')}"
            )
        return response.get("result")

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        while True:
            line = self._process.stdout.readline(MAX_AGENT_RPC_BYTES + 1)
            if not line:
                break
            self._responses.put(line)
            if len(line) > MAX_AGENT_RPC_BYTES:
                break
        self._responses.put(None)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(
                line.decode("utf-8", errors="replace").rstrip()
            )

    def _force_remove(self) -> None:
        subprocess.run(
            [self._docker, "rm", "--force", self._container_name],
            check=False,
            capture_output=True,
            timeout=10,
        )
        process = getattr(self, "_process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._close_process_streams()

    def _close_process_streams(self) -> None:
        process = getattr(self, "_process", None)
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
        process = subprocess.run(
            [self._docker, "inspect", self._container_name],
            check=False,
            capture_output=True,
            timeout=10,
        )
        return process.returncode == 0

    def _inspect_container_contract(self) -> dict[str, Any]:
        process = subprocess.run(
            [self._docker, "inspect", self._container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip()
            raise AgentAdapterError(
                "Could not inspect the running gVisor fixture agent: "
                f"{detail}"
            )
        try:
            records = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise AgentAdapterError(
                "Docker returned invalid fixture-agent container metadata"
            ) from error
        if (
            not isinstance(records, list)
            or len(records) != 1
            or not isinstance(records[0], dict)
        ):
            raise AgentAdapterError(
                "Docker returned an invalid fixture-agent container record"
            )
        return container_contract_probe(records[0], self.config)

    def _wait_for_container_contract(
        self,
        *,
        timeout: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_error: AgentAdapterError | None = None
        while time.monotonic() < deadline:
            try:
                return self._inspect_container_contract()
            except AgentAdapterError as error:
                last_error = error
                if self._process.poll() is not None:
                    break
                time.sleep(0.05)
        detail = str(last_error) if last_error is not None else self._diagnostic()
        raise AgentAdapterError(
            "Timed out before the gVisor fixture-agent Docker contract "
            f"could be verified: {detail}"
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
                f"Fixture-agent image {image!r} is unavailable or unpinned: "
                f"{detail}"
            )
        return image_id

    def _diagnostic(self) -> str:
        detail = " | ".join(self._stderr)
        return detail or f"exit={self._process.poll()}"

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


def _agent_container_name(run_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-.")
    return f"escape-lab-agent-{normalized}"[:120]


def _tmpfs_options(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {option for option in value.split(",") if option}


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
