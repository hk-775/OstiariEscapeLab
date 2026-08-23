"""Run orchestration, intervention, teardown, and replay inputs."""

from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Any

from escape_lab.adjudication import Adjudicator
from escape_lab.controls import ControlPlane
from escape_lab.evidence import EvidenceStore
from escape_lab.models import (
    ActionRequest,
    ControlProfile,
    Decision,
    RunMetrics,
    RunResult,
    RunValidity,
    Scenario,
)
from escape_lab.ostiari_bridge import OstiariBridge
from escape_lab.range import SyntheticRange
from escape_lab.registry import ScenarioRegistry
from escape_lab.reporting import write_run_report
from escape_lab.util import (
    redact,
    resolve_refs,
    sha256_json,
    slugify,
    utc_now,
    utc_now_iso,
    write_json,
)


class SafetyBoundaryError(RuntimeError):
    """Raised when a run would violate a hard lab safety boundary."""


class RunOrchestrator:
    def __init__(
        self,
        *,
        project_root: Path,
        registry: ScenarioRegistry,
        artifacts_root: Path | None = None,
        control_backend: str = "reference",
        ostiari_source: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.registry = registry
        self.artifacts_root = (
            artifacts_root.resolve()
            if artifacts_root is not None
            else (self.project_root / "artifacts").resolve()
        )
        self.control_backend = control_backend
        self.ostiari_source = ostiari_source

    def run(
        self,
        scenario_id: str,
        *,
        profile: ControlProfile,
        seed: int = 1,
        allow_c0: bool = False,
        replay_of: str | None = None,
    ) -> RunResult:
        scenario = self.registry.get(scenario_id)
        if profile == ControlProfile.C0 and not allow_c0:
            raise SafetyBoundaryError(
                "C0 disables runtime enforcement. Re-run with --allow-c0 only for "
                "reviewed synthetic scenarios."
            )
        self._preflight_safety(scenario)

        started_at = utc_now_iso()
        started_monotonic = time.monotonic()
        timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        run_id = (
            f"{scenario.scenario_id.lower()}-{profile.value.lower()}-"
            f"{timestamp}-{uuid.uuid4().hex[:8]}"
        )
        run_dir = self.artifacts_root / "runs" / run_id
        range_base = self.artifacts_root / "ranges"
        control_dir = self.artifacts_root / "control"
        control_dir.mkdir(parents=True, exist_ok=True)
        kill_file = control_dir / f"{run_id}.kill"

        synthetic_range = SyntheticRange(scenario, range_base, run_id)
        evidence = EvidenceStore(run_dir, run_id)
        external: OstiariBridge | None = None
        if self.control_backend == "ostiari":
            external = OstiariBridge(self.ostiari_source)
        elif self.control_backend != "reference":
            evidence.close()
            synthetic_range.teardown()
            raise ValueError(f"Unknown control backend: {self.control_backend}")

        control = ControlPlane(
            scenario=scenario,
            profile=profile,
            run_id=run_id,
            seed=seed,
            external=external,
        )
        adjudicator = Adjudicator(scenario)
        metrics = RunMetrics()
        notes: list[str] = []
        validity = RunValidity.VALID
        teardown: dict[str, Any] = {"complete": False}
        terminated = False
        variables: dict[str, Any] = {}
        unavailable_variables: set[str] = set()
        step_request_ids: dict[str, str] = {}
        random_source = random.Random(seed)

        run_manifest = {
            "run_id": run_id,
            "scenario": scenario.data,
            "scenario_digest": scenario.digest,
            "environment_digest": synthetic_range.environment_digest,
            "control_profile": profile.value,
            "control_backend": self.control_backend,
            "seed": seed,
            "model_configuration": {
                "adapter": "scripted-agent",
                "version": "1.0.0",
                "provider": "deterministic",
            },
            "runner": {
                "name": "ostiari-escape-lab",
                "version": "0.1.0",
                "range": "synthetic-range-v1",
            },
            "replay_of": replay_of,
            "started_at": started_at,
        }
        write_json(run_dir / "run-manifest.json", run_manifest)
        evidence.record(
            "run_started",
            {
                "scenario": scenario.scenario_id,
                "scenario_version": scenario.version,
                "scenario_digest": scenario.digest,
                "environment_digest": synthetic_range.environment_digest,
                "profile": profile.value,
                "seed": seed,
                "control_backend": self.control_backend,
            },
        )
        evidence.snapshot("before", synthetic_range.snapshot())

        try:
            steps = list(scenario.data["execution"]["steps"])
            if scenario.data["execution"].get("shuffle_equal_priority"):
                random_source.shuffle(steps)
            budgets = scenario.data["budgets"]

            for step in steps:
                elapsed = time.monotonic() - started_monotonic
                if kill_file.exists():
                    terminated = True
                    metrics.interventions += 1
                    notes.append("Operator kill switch requested termination")
                    evidence.record(
                        "intervention",
                        {
                            "type": "operator_kill",
                            "trigger": str(kill_file),
                            "result": "terminated",
                        },
                    )
                    break
                if elapsed > float(budgets["wall_clock_seconds"]):
                    terminated = True
                    metrics.interventions += 1
                    notes.append("Wall-clock budget exceeded")
                    evidence.record(
                        "intervention",
                        {
                            "type": "circuit_breaker",
                            "trigger": "wall_clock_budget",
                            "result": "terminated",
                        },
                    )
                    break
                if metrics.actions_requested >= int(budgets["tool_invocations"]):
                    terminated = True
                    metrics.interventions += 1
                    notes.append("Tool invocation budget exceeded")
                    evidence.record(
                        "intervention",
                        {
                            "type": "circuit_breaker",
                            "trigger": "tool_invocation_budget",
                            "result": "terminated",
                        },
                    )
                    break

                try:
                    params = resolve_refs(step.get("params", {}), variables)
                except KeyError as error:
                    if any(name in str(error) for name in unavailable_variables):
                        evidence.record(
                            "step_skipped",
                            {
                                "step_id": str(step.get("id", "")),
                                "reason": "dependency was prevented by an earlier intervention",
                                "detail": str(error),
                            },
                        )
                        continue
                    validity = RunValidity.INVALID
                    notes.append(str(error))
                    evidence.record(
                        "infrastructure_error",
                        {"stage": "resolve_refs", "error": str(error)},
                    )
                    break

                step_id = str(step["id"])
                parent_reference = step.get("parent_id")
                parent_id = (
                    step_request_ids.get(str(parent_reference), str(parent_reference))
                    if parent_reference is not None
                    else None
                )
                request = ActionRequest(
                    request_id=str(uuid.uuid4()),
                    trajectory_id=run_id,
                    parent_id=parent_id,
                    actor=str(step.get("actor", "agent")),
                    action=str(step["action"]),
                    params=params,
                    description=str(step.get("description", "")),
                    expected_authorized=bool(step.get("expected_authorized", True)),
                    prohibited_attempt=bool(step.get("prohibited_attempt", False)),
                    save_as=str(step["save_as"]) if step.get("save_as") else None,
                    step_id=step_id,
                )
                step_request_ids[step_id] = request.request_id
                metrics.actions_requested += 1
                if request.actor != "agent":
                    metrics.delegated_actions += 1
                if request.prohibited_attempt:
                    metrics.prohibited_attempts += 1

                preview = synthetic_range.preview(request)
                evidence.record(
                    "tool_request",
                    {
                        "request_id": request.request_id,
                        "parent_id": request.parent_id,
                        "actor": request.actor,
                        "action": request.action,
                        "arguments_digest": sha256_json(request.params),
                        "source_labels": list(preview.labels),
                        "declared_resource": preview.declared_resource,
                        "effective_resource": preview.effective_resource,
                        "declared_destination": preview.declared_destination,
                        "effective_destination": preview.effective_destination,
                        "facets": list(preview.facets),
                    },
                )
                checkpoint = synthetic_range.checkpoint()
                decision = control.evaluate(request, preview)
                evidence.record(
                    "policy_decision",
                    {
                        "request_id": request.request_id,
                        "decision": decision.decision.value,
                        "score": decision.score,
                        "aggregate_score": decision.aggregate_score,
                        "matched_rules": decision.matched_rules,
                        "rule_provenance": decision.rule_provenance,
                        "rationale": decision.rationale,
                        "latency_ms": decision.latency_ms,
                        "approval_id": decision.approval_id,
                        "approval_token_digest": decision.approval_token_digest,
                        "approval_expiry": decision.approval_expiry,
                        "original_tier": decision.original_tier,
                    },
                )

                is_intervention = decision.decision != Decision.ALLOW
                if is_intervention:
                    self._record_first_intervention(
                        metrics,
                        started_monotonic=started_monotonic,
                    )
                if decision.decision == Decision.APPROVE:
                    metrics.approvals += 1
                    evidence.record(
                        "approval",
                        {
                            "request_id": request.request_id,
                            "approval_id": decision.approval_id,
                            "scope": {
                                "action": request.action,
                                "resource": preview.effective_resource,
                                "destination": preview.effective_destination,
                                "params_digest": sha256_json(request.params),
                                "trajectory_id": request.trajectory_id,
                                "parent_id": request.parent_id,
                                "expiry": decision.approval_expiry,
                            },
                            "result": "approved",
                        },
                    )

                if not decision.permits_execution:
                    metrics.interventions += 1
                    if request.prohibited_attempt:
                        metrics.prohibited_attempts_prevented += 1
                    if request.expected_authorized:
                        metrics.false_blocks += 1
                    evidence.record(
                        "intervention",
                        {
                            "request_id": request.request_id,
                            "type": decision.decision.value,
                            "trigger": decision.rationale,
                            "scope": request.action,
                            "result": "not_executed",
                        },
                    )
                    control.record(
                        request,
                        preview,
                        decision,
                        executed=False,
                        result=None,
                    )
                    if request.save_as:
                        unavailable_variables.add(request.save_as)
                    if decision.decision in {Decision.SUSPEND, Decision.TERMINATE}:
                        terminated = True
                        break
                    continue

                execution_params = (
                    decision.transformed_params
                    if decision.transformed_params is not None
                    else request.params
                )
                if decision.decision == Decision.REDACT:
                    metrics.interventions += 1
                    if request.prohibited_attempt:
                        metrics.prohibited_attempts_prevented += 1
                    evidence.record(
                        "intervention",
                        {
                            "request_id": request.request_id,
                            "type": "redact",
                            "trigger": decision.rationale,
                            "result": "executed_with_transformed_parameters",
                            "transformed_arguments_digest": sha256_json(execution_params),
                        },
                    )

                try:
                    tool_result = synthetic_range.execute(request, execution_params)
                except Exception as error:
                    validity = RunValidity.INVALID
                    notes.append(f"Range execution failed for {request.action}: {error}")
                    evidence.record(
                        "infrastructure_error",
                        {
                            "request_id": request.request_id,
                            "stage": "range_execute",
                            "error": str(error),
                        },
                    )
                    control.record(
                        request,
                        preview,
                        decision,
                        executed=False,
                        result=None,
                    )
                    break

                metrics.actions_executed += 1
                evidence.record(
                    "tool_result",
                    {
                        "request_id": request.request_id,
                        "action": request.action,
                        "result_digest": sha256_json(tool_result),
                        "result_summary": redact(tool_result),
                        "state_delta": preview.state_delta,
                    },
                )
                if synthetic_range.emit_agent_events:
                    evidence.record(
                        "agent_observation",
                        {
                            "request_id": request.request_id,
                            "actor": request.actor,
                            "action": request.action,
                            "result_digest": sha256_json(tool_result),
                        },
                    )
                if request.save_as:
                    variables[request.save_as] = tool_result
                control.record(
                    request,
                    preview,
                    decision,
                    executed=True,
                    result=tool_result,
                )
                metrics.task_completed = bool(
                    synthetic_range.state.get("task", {}).get("completed", False)
                )

                new_hits = adjudicator.evaluate_state(
                    synthetic_range.state,
                    sequence=evidence.sequence,
                )
                for hit in new_hits:
                    evidence.record(
                        "outcome_observed",
                        {
                            "assertion_id": hit.assertion_id,
                            "level": hit.level.value,
                            "severity": hit.severity,
                            "description": hit.description,
                            "evidence_reference": hit.evidence_reference,
                        },
                    )
                if new_hits and profile == ControlProfile.C4:
                    synthetic_range.restore(checkpoint)
                    metrics.interventions += 1
                    evidence.record(
                        "intervention",
                        {
                            "request_id": request.request_id,
                            "type": "rollback",
                            "trigger": "post-condition assertion",
                            "result": "state_restored",
                        },
                    )
                    terminated = True
                    break

            evidence_hits = adjudicator.evaluate_evidence(
                evidence.counts,
                executed_actions=metrics.actions_executed,
                sequence=evidence.sequence,
            )
            for hit in evidence_hits:
                evidence.record(
                    "outcome_observed",
                    {
                        "assertion_id": hit.assertion_id,
                        "level": hit.level.value,
                        "severity": hit.severity,
                        "description": hit.description,
                        "evidence_reference": hit.evidence_reference,
                    },
                )

            evidence.snapshot("after", synthetic_range.snapshot())
            outcome = adjudicator.outcome(metrics.prohibited_attempts_prevented)
            teardown = synthetic_range.teardown()
            metrics.recovery_complete = bool(teardown.get("complete"))
            evidence.snapshot("post-teardown", synthetic_range.snapshot())

            anticipated_counts = evidence.counts
            anticipated_counts["run_completed"] = anticipated_counts.get("run_completed", 0) + 1
            metrics.evidence_complete = self._evidence_complete(
                scenario,
                anticipated_counts,
                metrics.actions_executed,
            )
            if not metrics.evidence_complete and not adjudicator.has_agent_caused_evidence_loss:
                validity = RunValidity.INVALID
                notes.append("Required evidence is incomplete")

            metrics.duration_ms = (time.monotonic() - started_monotonic) * 1000
            evidence.record(
                "run_completed",
                {
                    "outcome": outcome.value,
                    "validity": validity.value,
                    "terminated": terminated,
                    "metrics": metrics,
                    "teardown": teardown,
                    "assertion_ids": [hit.assertion_id for hit in adjudicator.hits],
                },
            )
            integrity = evidence.verify()
            if not integrity["valid"]:
                validity = RunValidity.INVALID
                notes.append("Evidence hash-chain verification failed")
            evidence.close()
            evidence.write_integrity_record(integrity)

            result = RunResult(
                run_id=run_id,
                scenario_id=scenario.scenario_id,
                scenario_version=scenario.version,
                scenario_digest=scenario.digest,
                environment_digest=synthetic_range.environment_digest,
                profile=profile,
                seed=seed,
                control_backend=self.control_backend,
                outcome=outcome,
                validity=validity,
                assertion_hits=adjudicator.hits,
                metrics=metrics,
                evidence_integrity=integrity,
                teardown=teardown,
                started_at=started_at,
                completed_at=utc_now_iso(),
                artifact_dir=run_dir,
                disclosure_status=str(
                    scenario.data.get("disclosure_status", "private")
                ),
                notes=notes,
            )
            write_run_report(result)
            return result
        finally:
            if external is not None:
                external.close()
            if not teardown.get("complete"):
                try:
                    teardown = synthetic_range.teardown()
                except Exception:
                    pass
            evidence.close()

    @staticmethod
    def _record_first_intervention(
        metrics: RunMetrics,
        *,
        started_monotonic: float,
    ) -> None:
        if metrics.time_to_first_intervention_ms is None:
            metrics.time_to_first_intervention_ms = (
                time.monotonic() - started_monotonic
            ) * 1000
            metrics.actions_to_first_intervention = metrics.actions_requested

    @staticmethod
    def _evidence_complete(
        scenario: Scenario,
        event_counts: dict[str, int],
        executed_actions: int,
    ) -> bool:
        instrumentation = scenario.data["instrumentation"]
        required = instrumentation.get("required_events", [])
        if any(event_counts.get(str(event_type), 0) < 1 for event_type in required):
            return False
        if instrumentation.get("agent_observation_per_action"):
            if event_counts.get("agent_observation", 0) < executed_actions:
                return False
        return True

    @staticmethod
    def _preflight_safety(scenario: Scenario) -> None:
        environment = scenario.data["environment"]
        network_policy = environment.get("network_policy", {})
        if network_policy.get("default") != "deny":
            raise SafetyBoundaryError("Scenario network policy must be deny-by-default")
        identities = environment.get("synthetic_identities", [])
        if not identities or not all(
            isinstance(identity, dict) and identity.get("synthetic") is True
            for identity in identities
        ):
            raise SafetyBoundaryError("Every scenario identity must be explicitly synthetic")
        if environment.get("host_mounts"):
            raise SafetyBoundaryError("Host mounts are prohibited in T1-T3 scenarios")
        if environment.get("production_credentials"):
            raise SafetyBoundaryError("Production credentials are prohibited")


def default_project_root() -> Path:
    current = Path.cwd().resolve()
    if (current / "scenarios" / "catalog.json").exists():
        return current
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "scenarios" / "catalog.json").exists():
        return package_root
    return current


def default_registry(project_root: Path) -> ScenarioRegistry:
    return ScenarioRegistry(project_root / "scenarios" / "catalog.json")


def experiment_id(scenario_ids: list[str], profiles: list[ControlProfile]) -> str:
    label = "-".join(scenario_ids) + "-" + "-".join(profile.value for profile in profiles)
    return f"{slugify(label)}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
