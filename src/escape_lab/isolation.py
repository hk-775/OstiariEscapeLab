"""Docker T2 preflight helpers.

The reference synthetic runner is safe for deterministic T0/T1 regression.
These helpers define the minimum command posture for a real disposable T2
container, but do not claim independent isolation certification.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DockerPreflight:
    available: bool
    daemon_reachable: bool
    server_version: str | None
    error: str | None


def hardened_docker_command(image: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        "--user",
        "65532:65532",
        image,
        "python",
        "-c",
        (
            "import os, socket; "
            "print({'uid': os.getuid(), 'cwd': os.getcwd(), "
            "'network_namespace': socket.gethostname()})"
        ),
    ]


def docker_preflight() -> DockerPreflight:
    if shutil.which("docker") is None:
        return DockerPreflight(
            available=False,
            daemon_reachable=False,
            server_version=None,
            error="docker executable not found",
        )
    try:
        process = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
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
            error=str(error),
        )
    if process.returncode != 0:
        return DockerPreflight(
            available=True,
            daemon_reachable=False,
            server_version=None,
            error=process.stderr.strip() or process.stdout.strip(),
        )
    return DockerPreflight(
        available=True,
        daemon_reachable=True,
        server_version=process.stdout.strip(),
        error=None,
    )


def probe_docker_image(image: str) -> dict[str, Any]:
    command = hardened_docker_command(image)
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

