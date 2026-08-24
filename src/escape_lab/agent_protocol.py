"""Bounded JSON records shared by isolated agent workers and their host proxy."""

from __future__ import annotations

from typing import Any

from escape_lab.agents import (
    AgentAction,
    AgentCompleted,
    AgentEvent,
    AgentObservation,
    AgentSkip,
    AgentUsage,
)
from escape_lab.util import to_primitive

MAX_AGENT_RPC_BYTES = 2 * 1024 * 1024

IDENTITY_ENVIRONMENT_NAMES = frozenset(
    {
        "AI21_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_CLIENT_SECRET",
        "AZURE_OPENAI_API_KEY",
        "COHERE_API_KEY",
        "FIREWORKS_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GOOGLE_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GROQ_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "OPENAI_API_KEY",
        "TOGETHER_API_KEY",
        "XAI_API_KEY",
    }
)


def encode_event(event: AgentEvent) -> dict[str, Any]:
    if isinstance(event, AgentAction):
        event_type = "action"
    elif isinstance(event, AgentSkip):
        event_type = "skip"
    elif isinstance(event, AgentCompleted):
        event_type = "completed"
    else:
        raise TypeError(f"Unsupported agent event: {type(event).__name__}")
    return {
        "type": event_type,
        "event": to_primitive(event),
    }


def decode_event(payload: Any) -> AgentEvent:
    if not isinstance(payload, dict):
        raise ValueError("Agent event payload must be an object")
    event_type = str(payload.get("type", ""))
    event = payload.get("event")
    if not isinstance(event, dict):
        raise ValueError("Agent event record must be an object")
    if event_type == "action":
        return decode_action(event)
    if event_type == "skip":
        return AgentSkip(
            step_id=str(event["step_id"]),
            reason=str(event["reason"]),
            detail=str(event["detail"]),
        )
    if event_type == "completed":
        content_digest = event.get("content_digest")
        return AgentCompleted(
            reason=str(event["reason"]),
            content_digest=(
                str(content_digest) if content_digest is not None else None
            ),
            usage=decode_usage(event.get("usage", {})),
        )
    raise ValueError(f"Unknown agent event type: {event_type!r}")


def decode_action(payload: Any) -> AgentAction:
    if not isinstance(payload, dict):
        raise ValueError("Agent action must be an object")
    return AgentAction(
        step_id=str(payload["step_id"]),
        actor=str(payload["actor"]),
        action=str(payload["action"]),
        params=dict(payload.get("params", {})),
        description=str(payload.get("description", "")),
        expected_authorized=bool(payload.get("expected_authorized", False)),
        prohibited_attempt=bool(payload.get("prohibited_attempt", True)),
        save_as=(
            str(payload["save_as"])
            if payload.get("save_as") is not None
            else None
        ),
        parent_step_id=(
            str(payload["parent_step_id"])
            if payload.get("parent_step_id") is not None
            else None
        ),
        tool_call_id=(
            str(payload["tool_call_id"])
            if payload.get("tool_call_id") is not None
            else None
        ),
        usage=decode_usage(payload.get("usage", {})),
    )


def decode_observation(payload: Any) -> AgentObservation:
    if not isinstance(payload, dict):
        raise ValueError("Agent observation must be an object")
    result = payload.get("result")
    return AgentObservation(
        decision=str(payload["decision"]),
        executed=bool(payload["executed"]),
        result=dict(result) if isinstance(result, dict) else None,
        error=(
            str(payload["error"])
            if payload.get("error") is not None
            else None
        ),
    )


def decode_usage(payload: Any) -> AgentUsage:
    if not isinstance(payload, dict):
        raise ValueError("Agent usage must be an object")
    return AgentUsage(
        prompt_tokens=int(payload.get("prompt_tokens", 0)),
        completion_tokens=int(payload.get("completion_tokens", 0)),
        total_tokens=int(payload.get("total_tokens", 0)),
        model_turns=int(payload.get("model_turns", 0)),
        cost_usd=(
            float(payload["cost_usd"])
            if payload.get("cost_usd") is not None
            else None
        ),
        retries=int(payload.get("retries", 0)),
        provider=(
            str(payload["provider"])
            if payload.get("provider") is not None
            else None
        ),
        model=(
            str(payload["model"])
            if payload.get("model") is not None
            else None
        ),
    )
