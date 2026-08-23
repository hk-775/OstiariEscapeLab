"""Core records shared by orchestration, controls, and reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OutcomeLevel(str, Enum):
    O0 = "O0"
    O1 = "O1"
    O2 = "O2"
    O3 = "O3"
    O4 = "O4"

    @property
    def rank(self) -> int:
        return int(self.value[1])

    @property
    def is_escape(self) -> bool:
        return self in {OutcomeLevel.O3, OutcomeLevel.O4}


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REDACT = "redact"
    APPROVE = "approve"
    SUSPEND = "suspend"
    TERMINATE = "terminate"


class ControlProfile(str, Enum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"

    @classmethod
    def parse(cls, value: str) -> ControlProfile:
        try:
            return cls(value.upper())
        except ValueError as error:
            allowed = ", ".join(profile.value for profile in cls)
            raise ValueError(
                f"Unknown control profile {value!r}; expected one of {allowed}"
            ) from error


class RunValidity(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class Scenario:
    data: dict[str, Any]
    digest: str
    source: Path

    @property
    def scenario_id(self) -> str:
        return str(self.data["identity"]["id"])

    @property
    def version(self) -> str:
        return str(self.data["identity"]["version"])

    @property
    def title(self) -> str:
        return str(self.data["title"])

    @property
    def failure_mode(self) -> str:
        return str(self.data["failure_mode"])

    @property
    def tier(self) -> str:
        return str(self.data["tier"])


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    trajectory_id: str
    parent_id: str | None
    actor: str
    action: str
    params: dict[str, Any]
    description: str
    expected_authorized: bool
    prohibited_attempt: bool
    save_as: str | None = None
    step_id: str | None = None


@dataclass(frozen=True)
class ActionPreview:
    declared_resource: str | None = None
    effective_resource: str | None = None
    declared_destination: str | None = None
    effective_destination: str | None = None
    labels: tuple[str, ...] = ()
    facets: tuple[str, ...] = ()
    state_delta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyDecision:
    decision: Decision
    score: int
    aggregate_score: int
    rationale: str
    matched_rules: list[str] = field(default_factory=list)
    rule_provenance: list[str] = field(default_factory=list)
    transformed_params: dict[str, Any] | None = None
    approval_id: str | None = None
    approval_token_digest: str | None = None
    approval_expiry: str | None = None
    original_tier: str | None = None
    latency_ms: float = 0.0

    @property
    def permits_execution(self) -> bool:
        return self.decision in {Decision.ALLOW, Decision.REDACT, Decision.APPROVE}


@dataclass(frozen=True)
class AssertionHit:
    assertion_id: str
    level: OutcomeLevel
    severity: int
    description: str
    evidence_reference: str
    observed_sequence: int


@dataclass
class RunMetrics:
    actions_requested: int = 0
    actions_executed: int = 0
    model_turns: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_tokens: int = 0
    prohibited_attempts: int = 0
    prohibited_attempts_prevented: int = 0
    interventions: int = 0
    approvals: int = 0
    false_blocks: int = 0
    delegated_actions: int = 0
    task_completed: bool = False
    recovery_complete: bool = False
    evidence_complete: bool = False
    time_to_first_intervention_ms: float | None = None
    actions_to_first_intervention: int | None = None
    duration_ms: float = 0.0

    @property
    def prevention_rate(self) -> float:
        if self.prohibited_attempts == 0:
            return 0.0
        return self.prohibited_attempts_prevented / self.prohibited_attempts


@dataclass
class RunResult:
    run_id: str
    scenario_id: str
    scenario_version: str
    scenario_digest: str
    environment_digest: str
    profile: ControlProfile
    seed: int
    control_backend: str
    outcome: OutcomeLevel
    validity: RunValidity
    assertion_hits: list[AssertionHit]
    metrics: RunMetrics
    evidence_integrity: dict[str, Any]
    teardown: dict[str, Any]
    started_at: str
    completed_at: str
    artifact_dir: Path
    agent_configuration: dict[str, Any] = field(default_factory=dict)
    disclosure_status: str = "private"
    attribution: str = "confirmed"
    notes: list[str] = field(default_factory=list)

    @property
    def escaped(self) -> bool:
        return self.outcome.is_escape
