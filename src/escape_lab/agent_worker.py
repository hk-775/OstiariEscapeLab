"""Offline AxonLLM fixture agent intended for a gVisor container."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from escape_lab.agent_protocol import (
    IDENTITY_ENVIRONMENT_NAMES,
    MAX_AGENT_RPC_BYTES,
    decode_action,
    decode_observation,
    encode_event,
)
from escape_lab.agents import AxonLLMAgentSession, AxonLLMConfig
from escape_lab.models import Scenario
from escape_lab.sandbox_worker import isolation_probe
from escape_lab.util import sha256_json, to_primitive

_IDENTITY_PATHS = (
    Path("/run/secrets"),
    Path("/var/run/secrets"),
    Path("/root/.aws/credentials"),
    Path("/root/.config/gcloud/application_default_credentials.json"),
    Path("/range/.env"),
)


class FixtureAgentWorker:
    """Own the real AxonLLM fixture loop inside the isolated boundary."""

    def __init__(self, axonllm_source: Path | None = None) -> None:
        self._axonllm_source = axonllm_source
        self.session: AxonLLMAgentSession | None = None

    def handle(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation == "init":
            if self.session is not None:
                raise RuntimeError("Fixture agent worker is already initialized")
            scenario_data = payload["scenario"]
            scenario_digest = str(payload["scenario_digest"])
            if sha256_json(scenario_data) != scenario_digest:
                raise ValueError("Scenario digest does not match the supplied manifest")
            scenario = Scenario(
                data=scenario_data,
                digest=scenario_digest,
                source=Path("agent-rpc"),
            )
            self.session = AxonLLMAgentSession(
                scenario,
                config=AxonLLMConfig(
                    source=self._axonllm_source,
                    mode="fixture",
                    fixture_transport="offline",
                    max_turns=int(payload.get("max_turns", 20)),
                ),
                seed=int(payload["seed"]),
            )
            boundary_probe = isolation_probe()
            identity = identity_probe()
            boundary_probe = agent_isolation_probe(boundary_probe)
            checks = dict(boundary_probe["checks"])
            checks.update(
                {
                    "offline_fixture_transport": (
                        self.session.metadata.get("fixture_transport") == "offline"
                        and self.session.metadata.get("network_required") is False
                    ),
                    "provider_auth_absent": (
                        self.session.metadata.get("provider_auth") == "none"
                    ),
                    "identity_environment_absent": identity[
                        "environment_variables"
                    ]
                    == [],
                    "identity_files_absent": identity["paths"] == [],
                }
            )
            boundary_probe["checks"] = checks
            required_checks = [
                *boundary_probe["required_in_boundary_checks"],
                "offline_fixture_transport",
                "provider_auth_absent",
                "identity_environment_absent",
                "identity_files_absent",
            ]
            boundary_probe["passed"] = all(
                checks[name] for name in required_checks
            )
            return {
                "agent_metadata": self.session.metadata,
                "isolation_probe": boundary_probe,
                "identity_probe": identity,
            }

        session = self._session()
        if operation == "next_event":
            return encode_event(session.next_event())
        if operation == "observe":
            session.observe(
                decode_action(payload["action"]),
                decode_observation(payload["observation"]),
            )
            return {"observed": True}
        if operation in {"close", "shutdown"}:
            session.close()
            self.session = None
            return {"closed": True}
        raise ValueError(f"Unknown fixture agent worker operation: {operation}")

    def _session(self) -> AxonLLMAgentSession:
        if self.session is None:
            raise RuntimeError("Fixture agent worker is not initialized")
        return self.session


def identity_probe() -> dict[str, Any]:
    """Report provider or cloud identity inputs accessible to the agent."""

    environment_variables = sorted(
        name
        for name in IDENTITY_ENVIRONMENT_NAMES
        if os.environ.get(name)
    )
    paths: list[str] = []
    inaccessible_paths: list[str] = []
    probe_errors: list[str] = []
    for path in _IDENTITY_PATHS:
        try:
            if path.is_file():
                paths.append(str(path))
            elif path.is_dir() and any(path.iterdir()):
                paths.append(str(path))
        except PermissionError:
            inaccessible_paths.append(str(path))
        except OSError:
            probe_errors.append(str(path))
    return {
        "passed": not environment_variables and not paths and not probe_errors,
        "environment_variables": environment_variables,
        "paths": paths,
        "inaccessible_paths": inaccessible_paths,
        "probe_errors": probe_errors,
    }


def agent_isolation_probe(probe: dict[str, Any]) -> dict[str, Any]:
    """Normalize gVisor observations and defer hidden OCI flags to Docker."""

    normalized = dict(probe)
    raw_checks = dict(probe.get("checks", {}))
    checks = dict(raw_checks)
    interface_states = probe.get("interface_states", {})
    if not isinstance(interface_states, dict):
        interface_states = {}
    ipv4_routes = probe.get("ipv4_non_loopback_routes", [])
    ipv6_routes = probe.get("ipv6_non_loopback_routes", [])
    non_loopback_interfaces_down = all(
        interface == "lo" or state == "down"
        for interface, state in interface_states.items()
    )
    checks["network_isolated"] = (
        non_loopback_interfaces_down
        and not ipv4_routes
        and not ipv6_routes
    )

    mount_options = probe.get("mount_options", {})
    if not isinstance(mount_options, dict):
        mount_options = {}
    observable_tmpfs_options = {"rw", "noexec", "nosuid"}
    for mountpoint, check_name in (
        ("/range", "range_tmpfs_hardened"),
        ("/tmp", "temp_tmpfs_hardened"),
    ):
        options = mount_options.get(mountpoint, [])
        checks[check_name] = observable_tmpfs_options.issubset(
            set(options) if isinstance(options, list) else set()
        )

    required_checks = [
        "non_root",
        "capabilities_dropped",
        "root_read_only",
        "network_isolated",
        "range_tmpfs_writable",
        "range_tmpfs_hardened",
        "temp_tmpfs_hardened",
    ]
    normalized.update(
        {
            "raw_checks": raw_checks,
            "checks": checks,
            "required_in_boundary_checks": required_checks,
            "host_verification_required": [
                "runsc_runtime",
                "network_none",
                "no_new_privileges",
                "tmpfs_nodev",
                "no_identity_injection",
            ],
            "passed": all(checks.get(name) is True for name in required_checks),
        }
    )
    return normalized


def _response_line(response: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(
            to_primitive(response),
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_AGENT_RPC_BYTES:
        raise RuntimeError("Fixture agent worker response exceeded the RPC limit")
    return encoded


def main() -> int:
    worker = FixtureAgentWorker()
    while True:
        line = sys.stdin.buffer.readline(MAX_AGENT_RPC_BYTES + 1)
        if not line:
            break
        if len(line) > MAX_AGENT_RPC_BYTES:
            sys.stdout.buffer.write(
                _response_line(
                    {
                        "id": None,
                        "ok": False,
                        "error": {
                            "type": "ValueError",
                            "message": "Fixture agent RPC request exceeded the limit",
                        },
                    }
                )
            )
            sys.stdout.buffer.flush()
            break

        request_id: Any = None
        should_exit = False
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("Fixture agent RPC message must be an object")
            request_id = message.get("id")
            operation = str(message["operation"])
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Fixture agent RPC payload must be an object")
            result = worker.handle(operation, payload)
            response = {"id": request_id, "ok": True, "result": result}
            should_exit = operation == "shutdown"
        except Exception as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
        sys.stdout.buffer.write(_response_line(response))
        sys.stdout.buffer.flush()
        if should_exit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
