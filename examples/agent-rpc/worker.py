#!/usr/bin/env python3
"""Minimal dependency-free Ostiari Escape Lab Agent-RPC worker."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

MAX_RPC_BYTES = 2 * 1024 * 1024


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve(value: Any, variables: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            return variables[str(value["$ref"])]
        return {
            str(key): _resolve(item, variables)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve(item, variables) for item in value]
    return value


class ExampleAgent:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.index = 0
        self.variables: dict[str, Any] = {}
        self.unavailable: set[str] = set()

    def handle(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation == "init":
            scenario = payload["scenario"]
            if _digest(scenario) != str(payload["scenario_digest"]):
                raise ValueError("Scenario digest mismatch")
            execution = scenario.get("execution", {})
            self.steps = [
                dict(step) for step in execution.get("steps", [])
            ]
            return {
                "agent_metadata": {
                    "name": "example-agent-rpc",
                    "version": "1.0.0",
                    "mode": "offline-reference",
                }
            }
        if operation == "next_event":
            return self._next_event()
        if operation == "observe":
            action = payload.get("action", {})
            observation = payload.get("observation", {})
            save_as = action.get("save_as")
            if save_as:
                if observation.get("executed") and isinstance(
                    observation.get("result"),
                    dict,
                ):
                    self.variables[str(save_as)] = observation["result"]
                else:
                    self.unavailable.add(str(save_as))
            return {"observed": True}
        if operation == "shutdown":
            return {"closed": True}
        raise ValueError(f"Unknown operation: {operation}")

    def _next_event(self) -> dict[str, Any]:
        while self.index < len(self.steps):
            step = self.steps[self.index]
            self.index += 1
            try:
                params = _resolve(step.get("params", {}), self.variables)
            except KeyError as error:
                return {
                    "type": "skip",
                    "event": {
                        "step_id": str(step.get("id", "")),
                        "reason": "dependency was prevented",
                        "detail": str(error),
                    },
                }
            return {
                "type": "action",
                "event": {
                    "step_id": str(step["id"]),
                    "actor": str(step["actor"]),
                    "action": str(step["action"]),
                    "params": params,
                    "description": str(step.get("description", "")),
                    "expected_authorized": bool(
                        step.get("expected_authorized", False)
                    ),
                    "prohibited_attempt": bool(
                        step.get("prohibited_attempt", True)
                    ),
                    "save_as": step.get("save_as"),
                    "parent_step_id": step.get("parent_id"),
                    "tool_call_id": None,
                    "usage": {},
                },
            }
        return {
            "type": "completed",
            "event": {
                "reason": "example trajectory complete",
                "content_digest": None,
                "usage": {},
            },
        }


def _response_line(value: dict[str, Any]) -> bytes:
    encoded = (
        json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_RPC_BYTES:
        raise RuntimeError("Agent-RPC response exceeded the size limit")
    return encoded


def main() -> int:
    agent = ExampleAgent()
    while True:
        line = sys.stdin.buffer.readline(MAX_RPC_BYTES + 1)
        if not line:
            break
        request_id: Any = None
        operation = ""
        try:
            if len(line) > MAX_RPC_BYTES:
                raise ValueError("Agent-RPC request exceeded the size limit")
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Agent-RPC request must be an object")
            request_id = request.get("id")
            operation = str(request["operation"])
            payload = request.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Agent-RPC payload must be an object")
            response = {
                "id": request_id,
                "ok": True,
                "result": agent.handle(operation, payload),
            }
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
        if operation == "shutdown" and response["ok"]:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
