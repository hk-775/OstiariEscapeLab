"""Docker T2 preflight helpers.

The reference synthetic runner is safe for deterministic T0/T1 regression.
These helpers define the minimum command posture for a real disposable T2
container, but do not claim independent isolation certification.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DockerPreflight:
    available: bool
    daemon_reachable: bool
    server_version: str | None
    runtimes: tuple[str, ...]
    error: str | None


def hardened_docker_command(
    image: str,
    *,
    runtime: str | None = None,
    docker_binary: str = "docker",
) -> list[str]:
    command = [
        docker_binary,
        "run",
        "--rm",
        "--pull",
        "never",
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
        "64",
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=32m",
        "--user",
        "65532:65532",
    ]
    if runtime:
        command.extend(["--runtime", runtime])
    command.extend(
        [
        image,
        "python",
        "-c",
        (
            "import os, socket; "
            "print({'uid': os.getuid(), 'cwd': os.getcwd(), "
            "'network_namespace': socket.gethostname()})"
        ),
        ]
    )
    return command


def docker_preflight(docker_binary: str = "docker") -> DockerPreflight:
    executable = shutil.which(docker_binary)
    if executable is None:
        return DockerPreflight(
            available=False,
            daemon_reachable=False,
            server_version=None,
            runtimes=(),
            error=f"{docker_binary} executable not found",
        )
    try:
        process = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return DockerPreflight(
            available=True,
            daemon_reachable=False,
            server_version=None,
            runtimes=(),
            error=str(error),
        )
    if process.returncode != 0:
        return DockerPreflight(
            available=True,
            daemon_reachable=False,
            server_version=None,
            runtimes=(),
            error=process.stderr.strip() or process.stdout.strip(),
        )
    runtimes: tuple[str, ...] = ()
    try:
        runtimes_process = subprocess.run(
            [executable, "info", "--format", "{{json .Runtimes}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if runtimes_process.returncode == 0:
            payload = json.loads(runtimes_process.stdout)
            if isinstance(payload, dict):
                runtimes = tuple(sorted(str(name) for name in payload))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return DockerPreflight(
        available=True,
        daemon_reachable=True,
        server_version=process.stdout.strip(),
        runtimes=runtimes,
        error=None,
    )


def probe_docker_image(
    image: str,
    *,
    runtime: str | None = None,
    docker_binary: str = "docker",
) -> dict[str, Any]:
    command = hardened_docker_command(
        image,
        runtime=runtime,
        docker_binary=docker_binary,
    )
    process = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {
        "success": process.returncode == 0,
        "returncode": process.returncode,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
        "command": command,
    }
