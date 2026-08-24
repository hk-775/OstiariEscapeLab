"""JSON-line range worker intended to run inside an isolated container."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from escape_lab.models import ActionRequest, Scenario
from escape_lab.range import SyntheticRange
from escape_lab.util import sha256_json, to_primitive


class SandboxWorker:
    """Own range state inside the container boundary."""

    def __init__(self, range_base: Path = Path("/range")) -> None:
        self.range: SyntheticRange | None = None
        self.range_base = range_base

    def handle(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation == "init":
            if self.range is not None:
                raise RuntimeError("Sandbox worker is already initialized")
            scenario_data = payload["scenario"]
            scenario_digest = str(payload["scenario_digest"])
            if sha256_json(scenario_data) != scenario_digest:
                raise ValueError("Scenario digest does not match the supplied manifest")
            scenario = Scenario(
                data=scenario_data,
                digest=scenario_digest,
                source=Path("sandbox-rpc"),
            )
            self.range = SyntheticRange(
                scenario,
                self.range_base,
                str(payload["run_id"]),
            )
            return {
                "environment_digest": self.range.environment_digest,
                "isolation_probe": isolation_probe(),
            }

        execution_range = self._range()
        if operation == "snapshot":
            return execution_range.snapshot()
        if operation == "checkpoint":
            return execution_range.checkpoint()
        if operation == "restore":
            execution_range.restore(dict(payload["checkpoint"]))
            return {"restored": True}
        if operation == "preview":
            request = _request(payload["request"])
            return to_primitive(execution_range.preview(request))
        if operation == "execute":
            request = _request(payload["request"])
            params = payload.get("params")
            return execution_range.execute(
                request,
                dict(params) if isinstance(params, dict) else None,
            )
        if operation == "emit_agent_events":
            return execution_range.emit_agent_events
        if operation == "task_completed":
            return execution_range.task_completed
        if operation == "teardown":
            return execution_range.teardown()
        if operation == "shutdown":
            if self.range is not None:
                self.range.teardown()
            return {"shutdown": True}
        raise ValueError(f"Unknown sandbox worker operation: {operation}")

    def _range(self) -> SyntheticRange:
        if self.range is None:
            raise RuntimeError("Sandbox worker is not initialized")
        return self.range


def isolation_probe() -> dict[str, Any]:
    """Verify the expected restrictions from inside the container."""

    status: dict[str, str] = {}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                status[key] = value.strip()
    except OSError:
        pass

    mount_options: dict[str, set[str]] = {}
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[1] in {"/", "/range", "/tmp"}:
                mount_options[fields[1]] = set(fields[3].split(","))
    except OSError:
        pass
    root_options = mount_options.get("/", set())
    required_tmpfs_options = {"rw", "noexec", "nosuid", "nodev"}

    interface_states: dict[str, str] = {}
    try:
        for line in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]:
            if ":" in line:
                interface = line.split(":", 1)[0].strip()
                state_path = Path("/sys/class/net") / interface / "operstate"
                interface_states[interface] = state_path.read_text(
                    encoding="utf-8"
                ).strip()
    except OSError:
        pass

    ipv4_non_loopback_routes: list[str] = []
    try:
        route_lines = Path("/proc/net/route").read_text(
            encoding="utf-8"
        ).splitlines()
        for line in route_lines[1:]:
            fields = line.split()
            if fields and fields[0] != "lo":
                ipv4_non_loopback_routes.append(line.strip())
    except OSError:
        pass

    ipv6_non_loopback_routes: list[str] = []
    try:
        for line in Path("/proc/net/ipv6_route").read_text(
            encoding="utf-8"
        ).splitlines():
            fields = line.split()
            if fields and fields[-1] != "lo":
                ipv6_non_loopback_routes.append(line.strip())
    except OSError:
        pass

    non_loopback_interfaces_down = all(
        interface == "lo" or state == "down"
        for interface, state in interface_states.items()
    )
    network_isolated = (
        interface_states.get("lo") in {"unknown", "up"}
        and non_loopback_interfaces_down
        and not ipv4_non_loopback_routes
        and not ipv6_non_loopback_routes
    )

    range_writable = False
    probe_path = Path("/range/.isolation-probe")
    try:
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
        range_writable = True
    except OSError:
        pass

    checks = {
        "non_root": os.getuid() != 0,
        "capabilities_dropped": status.get("CapEff") in {
            "0000000000000000",
            "00000000000000000000000000000000",
        },
        "no_new_privileges": status.get("NoNewPrivs") == "1",
        "root_read_only": "ro" in root_options,
        "network_isolated": network_isolated,
        "range_tmpfs_writable": range_writable,
        "range_tmpfs_hardened": required_tmpfs_options.issubset(
            mount_options.get("/range", set())
        ),
        "temp_tmpfs_hardened": required_tmpfs_options.issubset(
            mount_options.get("/tmp", set())
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "interface_states": dict(sorted(interface_states.items())),
        "ipv4_non_loopback_routes": ipv4_non_loopback_routes,
        "ipv6_non_loopback_routes": ipv6_non_loopback_routes,
        "mount_options": {
            mountpoint: sorted(
                option for option in options if "=" not in option
            )
            for mountpoint, options in sorted(mount_options.items())
        },
    }


def _request(payload: Any) -> ActionRequest:
    if not isinstance(payload, dict):
        raise ValueError("Action request must be an object")
    return ActionRequest(
        request_id=str(payload["request_id"]),
        trajectory_id=str(payload["trajectory_id"]),
        parent_id=(
            str(payload["parent_id"])
            if payload.get("parent_id") is not None
            else None
        ),
        actor=str(payload["actor"]),
        action=str(payload["action"]),
        params=dict(payload.get("params", {})),
        description=str(payload.get("description", "")),
        expected_authorized=bool(payload.get("expected_authorized", False)),
        prohibited_attempt=bool(payload.get("prohibited_attempt", True)),
        save_as=(
            str(payload["save_as"]) if payload.get("save_as") is not None else None
        ),
        step_id=(
            str(payload["step_id"]) if payload.get("step_id") is not None else None
        ),
    )


def main() -> int:
    worker = SandboxWorker()
    for line in sys.stdin:
        request_id: Any = None
        should_exit = False
        try:
            message = json.loads(line)
            request_id = message.get("id")
            operation = str(message["operation"])
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Sandbox payload must be an object")
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
        sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
        sys.stdout.flush()
        if should_exit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
