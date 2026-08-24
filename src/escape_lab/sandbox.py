"""Pluggable execution ranges, including a hardened OCI/gVisor worker."""

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
from pathlib import Path
from typing import Any, Protocol

from escape_lab.models import ActionPreview, ActionRequest, Scenario
from escape_lab.range import SyntheticRange
from escape_lab.util import sha256_json, to_primitive


class SandboxError(RuntimeError):
    """Raised when an execution range cannot be created or trusted."""


class RangeSession(Protocol):
    """Execution boundary consumed by the run orchestrator."""

    environment_digest: str

    @property
    def metadata(self) -> dict[str, Any]:
        """Return redaction-safe runtime identity and isolation evidence."""

    @property
    def emit_agent_events(self) -> bool:
        """Whether in-band agent observations remain available."""

    @property
    def task_completed(self) -> bool:
        """Whether the authorized task reached its completion marker."""

    def snapshot(self) -> dict[str, Any]:
        """Capture protected range state."""

    def checkpoint(self) -> dict[str, Any]:
        """Capture state suitable for rollback."""

    def restore(self, checkpoint: dict[str, Any]) -> None:
        """Restore a prior checkpoint."""

    def preview(self, request: ActionRequest) -> ActionPreview:
        """Resolve effective resources and risk facets without side effects."""

    def execute(
        self,
        request: ActionRequest,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one mediated action inside the range."""

    def teardown(self) -> dict[str, Any]:
        """Destroy the range and report verified cleanup."""


class RangeFactory(Protocol):
    """Create one execution range per run."""

    def create(
        self,
        scenario: Scenario,
        range_base: Path,
        run_id: str,
    ) -> RangeSession:
        """Create and verify a new execution boundary."""


class SyntheticRangeFactory:
    """Create the deterministic in-process reference range."""

    def create(
        self,
        scenario: Scenario,
        range_base: Path,
        run_id: str,
    ) -> SyntheticRange:
        return SyntheticRange(scenario, range_base, run_id)


@dataclass(frozen=True)
class DockerSandboxConfig:
    """Configuration for a locked-down Docker or gVisor range worker."""

    image: str | None = None
    runtime: str | None = None
    docker_binary: str = "docker"
    memory: str = "256m"
    cpus: str = "0.5"
    pids_limit: int = 64
    range_tmpfs: str = "64m"
    temp_tmpfs: str = "32m"
    startup_timeout_seconds: float = 20.0
    rpc_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.pids_limit < 8:
            raise ValueError("Sandbox pids_limit must be at least 8")
        if self.startup_timeout_seconds <= 0 or self.rpc_timeout_seconds <= 0:
            raise ValueError("Sandbox timeouts must be positive")


class DockerSandboxFactory:
    """Create ranges backed by a hardened Docker-compatible runtime."""

    def __init__(self, config: DockerSandboxConfig) -> None:
        self.config = config

    def create(
        self,
        scenario: Scenario,
        range_base: Path,
        run_id: str,
    ) -> DockerSandboxRange:
        return DockerSandboxRange(
            scenario,
            range_base,
            run_id,
            config=self.config,
        )


def docker_worker_command(
    *,
    docker_binary: str,
    image_reference: str,
    container_name: str,
    runtime: str | None,
    memory: str,
    cpus: str,
    pids_limit: int,
    range_tmpfs: str,
    temp_tmpfs: str,
    run_id: str,
) -> list[str]:
    """Build the shell-free, default-deny range-worker command."""

    command = [
        docker_binary,
        "run",
        "--rm",
        "--interactive",
        "--pull",
        "never",
        "--name",
        container_name,
        "--hostname",
        "escape-lab",
        "--label",
        f"io.ostiari.escape-lab.run-id={run_id}",
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
            f"/range:rw,noexec,nosuid,nodev,size={range_tmpfs},"
            "mode=0700,uid=65532,gid=65532"
        ),
        "--user",
        "65532:65532",
        "--workdir",
        "/range",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONUNBUFFERED=1",
    ]
    if runtime:
        command.extend(["--runtime", runtime])
    command.extend(
        [
            image_reference,
            "python",
            "-m",
            "escape_lab.sandbox_worker",
        ]
    )
    return command


def sandbox_environment_digest(
    inner_environment_digest: str,
    metadata: dict[str, Any],
) -> str:
    """Hash stable sandbox identity while excluding per-run diagnostics."""

    probe = metadata.get("isolation_probe", {})
    probe_checks = (
        probe.get("checks", {})
        if isinstance(probe, dict)
        else {}
    )
    return sha256_json(
        {
            "inner_environment_digest": inner_environment_digest,
            "backend": metadata.get("backend"),
            "runtime": metadata.get("runtime"),
            "docker_server_version": metadata.get("docker_server_version"),
            "image_id": metadata.get("image_id"),
            "network": metadata.get("network"),
            "ipc": metadata.get("ipc"),
            "root_filesystem": metadata.get("root_filesystem"),
            "user": metadata.get("user"),
            "capabilities": metadata.get("capabilities"),
            "no_new_privileges": metadata.get("no_new_privileges"),
            "limits": metadata.get("limits"),
            "isolation_probe_checks": probe_checks,
        }
    )


class DockerSandboxRange:
    """Run range state and tools inside a hardened OCI/gVisor container."""

    def __init__(
        self,
        scenario: Scenario,
        range_base: Path,
        run_id: str,
        *,
        config: DockerSandboxConfig,
    ) -> None:
        self.scenario = scenario
        self.range_base = range_base.resolve()
        self.range_base.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.config = config
        self._destroyed = False
        self._final_snapshot: dict[str, Any] | None = None
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=50)

        self._docker = self._resolve_docker_binary(config.docker_binary)
        self._server_version = self._docker_server_version()
        self._available_runtimes = self._docker_runtimes()
        if config.runtime and config.runtime not in self._available_runtimes:
            available = ", ".join(sorted(self._available_runtimes)) or "none"
            raise SandboxError(
                f"Requested OCI runtime {config.runtime!r} is unavailable; "
                f"configured runtimes: {available}"
            )

        requested_image = config.image or str(
            scenario.data["environment"].get("image", "")
        )
        if not requested_image:
            raise SandboxError("A sandbox image is required")
        self._image_id = self._inspect_image_id(requested_image)
        self._container_name = _container_name(run_id)
        command = docker_worker_command(
            docker_binary=self._docker,
            image_reference=self._image_id,
            container_name=self._container_name,
            runtime=config.runtime,
            memory=config.memory,
            cpus=config.cpus,
            pids_limit=config.pids_limit,
            range_tmpfs=config.range_tmpfs,
            temp_tmpfs=config.temp_tmpfs,
            run_id=run_id,
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as error:
            raise SandboxError(f"Could not start sandbox worker: {error}") from error
        if (
            self._process.stdin is None
            or self._process.stdout is None
            or self._process.stderr is None
        ):
            self._force_remove()
            raise SandboxError("Sandbox worker did not expose its control pipes")

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
            initialized = self._rpc(
                "init",
                {
                    "scenario": scenario.data,
                    "scenario_digest": scenario.digest,
                    "run_id": run_id,
                },
                timeout=config.startup_timeout_seconds,
            )
            probe = initialized.get("isolation_probe")
            if not isinstance(probe, dict) or not probe.get("passed"):
                raise SandboxError(
                    "Sandbox worker failed its in-boundary isolation probe: "
                    f"{probe}"
                )
            inner_digest = str(initialized["environment_digest"])
            backend = (
                "gvisor"
                if config.runtime and config.runtime.startswith("runsc")
                else "docker"
            )
            self._metadata = {
                "backend": backend,
                "runtime": config.runtime or "docker-default",
                "docker_server_version": self._server_version,
                "image_requested": requested_image,
                "image_id": self._image_id,
                "container_name": self._container_name,
                "network": "none",
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
                    "range_tmpfs": config.range_tmpfs,
                    "temp_tmpfs": config.temp_tmpfs,
                },
                "isolation_probe": probe,
            }
            self.environment_digest = sandbox_environment_digest(
                inner_digest,
                self._metadata,
            )
        except BaseException:
            self._force_remove()
            raise

    @property
    def metadata(self) -> dict[str, Any]:
        return deepcopy(self._metadata)

    @property
    def emit_agent_events(self) -> bool:
        return bool(self._rpc("emit_agent_events"))

    @property
    def task_completed(self) -> bool:
        return bool(self._rpc("task_completed"))

    def snapshot(self) -> dict[str, Any]:
        if self._destroyed and self._final_snapshot is not None:
            return deepcopy(self._final_snapshot)
        result = self._rpc("snapshot")
        if not isinstance(result, dict):
            raise SandboxError("Sandbox snapshot was not an object")
        return result

    def checkpoint(self) -> dict[str, Any]:
        result = self._rpc("checkpoint")
        if not isinstance(result, dict):
            raise SandboxError("Sandbox checkpoint was not an object")
        return result

    def restore(self, checkpoint: dict[str, Any]) -> None:
        self._rpc("restore", {"checkpoint": checkpoint})

    def preview(self, request: ActionRequest) -> ActionPreview:
        result = self._rpc(
            "preview",
            {"request": to_primitive(request)},
        )
        if not isinstance(result, dict):
            raise SandboxError("Sandbox preview was not an object")
        return ActionPreview(
            declared_resource=result.get("declared_resource"),
            effective_resource=result.get("effective_resource"),
            declared_destination=result.get("declared_destination"),
            effective_destination=result.get("effective_destination"),
            labels=tuple(result.get("labels", [])),
            facets=tuple(result.get("facets", [])),
            state_delta=dict(result.get("state_delta", {})),
        )

    def execute(
        self,
        request: ActionRequest,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._rpc(
            "execute",
            {
                "request": to_primitive(request),
                "params": params,
            },
        )
        if not isinstance(result, dict):
            raise SandboxError("Sandbox tool result was not an object")
        return result

    def teardown(self) -> dict[str, Any]:
        if self._destroyed:
            return {
                "complete": True,
                "already_destroyed": True,
                "container_removed": True,
            }
        inner: dict[str, Any] = {"complete": False}
        error: BaseException | None = None
        try:
            response = self._rpc("teardown")
            if isinstance(response, dict):
                inner = response
            final_snapshot = self._rpc("snapshot")
            if isinstance(final_snapshot, dict):
                self._final_snapshot = final_snapshot
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
        self._destroyed = True
        result = {
            **inner,
            "container_removed": removed,
            "runtime": self.config.runtime or "docker-default",
            "image_id": self._image_id,
            "isolation_probe_passed": bool(
                self._metadata.get("isolation_probe", {}).get("passed")
            ),
        }
        result["complete"] = bool(inner.get("complete") and removed)
        if error is not None:
            raise SandboxError(f"Sandbox teardown failed: {error}") from error
        return result

    def _rpc(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if self._destroyed:
            raise SandboxError("Sandbox range has already been destroyed")
        self._request_id += 1
        request_id = self._request_id
        message = {
            "id": request_id,
            "operation": operation,
            "payload": payload or {},
        }
        assert self._process.stdin is not None
        try:
            with self._write_lock:
                self._process.stdin.write(
                    json.dumps(message, sort_keys=True, ensure_ascii=False) + "\n"
                )
                self._process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise SandboxError(
                f"Sandbox worker control channel failed: {self._diagnostic()}"
            ) from error

        wait_seconds = timeout or self.config.rpc_timeout_seconds
        try:
            line = self._responses.get(timeout=wait_seconds)
        except queue.Empty as error:
            self._force_remove()
            raise SandboxError(
                f"Sandbox worker timed out during {operation!r}"
            ) from error
        if line is None:
            raise SandboxError(
                f"Sandbox worker exited during {operation!r}: {self._diagnostic()}"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise SandboxError("Sandbox worker emitted invalid JSON") from error
        if response.get("id") != request_id:
            raise SandboxError("Sandbox worker response correlation failed")
        if not response.get("ok"):
            detail = response.get("error", {})
            raise SandboxError(
                f"Sandbox worker rejected {operation!r}: "
                f"{detail.get('type', 'error')}: {detail.get('message', '')}"
            )
        return response.get("result")

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._responses.put(line)
        self._responses.put(None)

    def _read_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in self._process.stderr:
            self._stderr.append(line.rstrip())

    def _force_remove(self) -> None:
        subprocess.run(
            [self._docker, "rm", "--force", self._container_name],
            check=False,
            capture_output=True,
            text=True,
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
            text=True,
            timeout=10,
        )
        return process.returncode == 0

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
            raise SandboxError(f"Docker daemon is unavailable: {detail}")
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
            raise SandboxError(f"Could not inspect Docker runtimes: {detail}")
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as error:
            raise SandboxError("Docker returned invalid runtime metadata") from error
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
            raise SandboxError(
                f"Sandbox image {image!r} is unavailable or unpinned: {detail}"
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
            raise SandboxError(f"Docker executable does not exist: {value}")
        resolved = shutil.which(value)
        if resolved is None:
            raise SandboxError(f"Docker executable was not found: {value}")
        return resolved


def _container_name(run_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-.")
    return f"escape-lab-{normalized}"[:120]
