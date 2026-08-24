"""Run orchestration, intervention, teardown, and replay inputs."""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from escape_lab import __version__
from escape_lab.adjudication import Adjudicator
from escape_lab.agents import (
    AgentAdapterError,
    AgentCompleted,
    AgentFactory,
    AgentObservation,
    AgentSession,
    AgentSkip,
    ScriptedAgentFactory,
)
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
from escape_lab.registry import ScenarioRegistry
from escape_lab.reporting import write_run_report
from escape_lab.sandbox import RangeFactory, RangeSession, SyntheticRangeFactory
from escape_lab.util import (
    redact,
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
        agent_factory: AgentFactory | None = None,
        range_factory: RangeFactory | None = None,
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
        self.agent_factory = agent_factory or ScriptedAgentFactory()
        self.range_factory = range_factory or SyntheticRangeFactory()

    def run(
        self,
        scenario_id: str,
        *,
        profile: ControlProfile,
        seed: int = 1,
        allow_c0: bool = False,
        replay_of: str | None = None,
        budget_overrides: dict[str, int | float] | None = None,
        scenario_override: Scenario | None = None,
        variant_id: str | None = None,
    ) -> RunResult:
        scenario = scenario_override or self.registry.get(scenario_id)
        if scenario.scenario_id != scenario_id.upper():
            raise ValueError(
                "Scenario override identity does not match requested scenario"
            )
        if profile == ControlProfile.C0 and not allow_c0:
            raise SafetyBoundaryError(
                "C0 disables runtime enforcement. Re-run with --allow-c0 only for "
                "reviewed synthetic scenarios."
            )
        self._preflight_safety(scenario)
        budgets = self._effective_budgets(
            scenario.data["budgets"],
            budget_overrides,
        )

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

        execution_range = self.range_factory.create(
            scenario,
            range_base,
            run_id,
        )
        try:
            evidence = EvidenceStore(run_dir, run_id)
        except BaseException:
            self._best_effort(execution_range.teardown)
            raise
        external: OstiariBridge | None = None
        agent_session: AgentSession | None = None
        teardown: dict[str, Any] = {"complete": False}
        try:
            if self.control_backend == "ostiari":
                external = OstiariBridge(self.ostiari_source)
            elif self.control_backend != "reference":
                raise ValueError(
                    f"Unknown control backend: {self.control_backend}"
                )

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
            terminated = False
            step_request_ids: dict[str, str] = {}
            agent_session = self.agent_factory.start(
                scenario,
                seed=seed,
                run_id=run_id,
            )
            agent_configuration = redact(agent_session.metadata)
            agent_runtime = agent_session.metadata.get("runtime", {})
            agent_runtime_digest = (
                str(agent_runtime.get("digest"))
                if isinstance(agent_runtime, dict)
                and agent_runtime.get("digest")
                else None
            )
            environment_digest = execution_range.environment_digest
            if agent_runtime_digest is not None:
                environment_digest = sha256_json(
                    {
                        "range_environment_digest": (
                            execution_range.environment_digest
                        ),
                        "agent_runtime_digest": agent_runtime_digest,
                    }
                )

            run_manifest = {
                "run_id": run_id,
                "scenario": scenario.data,
                "scenario_digest": scenario.digest,
                "environment_digest": environment_digest,
                "control_profile": profile.value,
                "control_backend": self.control_backend,
                "seed": seed,
                "budgets": budgets,
                "budget_overrides": budget_overrides or {},
                "variant_id": variant_id,
                "model_configuration": agent_configuration,
                "runner": {
                    "name": "ostiari-escape-lab",
                    "version": __version__,
                    "range": execution_range.metadata,
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
                    "environment_digest": environment_digest,
                    "profile": profile.value,
                    "seed": seed,
                    "control_backend": self.control_backend,
                    "budgets": budgets,
                    "variant_id": variant_id,
                    "agent_configuration": agent_configuration,
                    "range_configuration": execution_range.metadata,
                },
            )
            evidence.snapshot("before", execution_range.snapshot())
        except BaseException as error:
            try:
                evidence.record(
                    "infrastructure_error",
                    {"stage": "run_setup", "error": str(error)},
                )
            except Exception:
                pass
            self._cleanup_run_resources(
                agent_session=agent_session,
                external=external,
                execution_range=execution_range,
                evidence=evidence,
                teardown=teardown,
            )
            raise

        try:
            assert agent_session is not None
            try:
                while True:
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
                    action_limit = min(
                        int(budgets["steps"]),
                        int(budgets["tool_invocations"]),
                    )
                    if metrics.actions_requested >= action_limit:
                        terminated = True
                        metrics.interventions += 1
                        notes.append("Agent action budget exceeded")
                        evidence.record(
                            "intervention",
                            {
                                "type": "circuit_breaker",
                                "trigger": "agent_action_budget",
                                "result": "terminated",
                            },
                        )
                        break

                    event = agent_session.next_event()
                    if isinstance(event, AgentSkip):
                        evidence.record(
                            "step_skipped",
                            {
                                "step_id": event.step_id,
                                "reason": event.reason,
                                "detail": event.detail,
                            },
                        )
                        continue

                    self._add_agent_usage(metrics, event.usage)
                    if event.usage.model_turns:
                        evidence.record(
                            "agent_turn",
                            {
                                "provider": event.usage.provider,
                                "model": event.usage.model,
                                "prompt_tokens": event.usage.prompt_tokens,
                                "completion_tokens": event.usage.completion_tokens,
                                "total_tokens": event.usage.total_tokens,
                                "cost_usd": event.usage.cost_usd,
                                "retries": event.usage.retries,
                            },
                        )
                    if (
                        event.usage.model_turns
                        and event.usage.cost_usd is None
                        and agent_configuration.get("mode") == "live"
                    ):
                        validity = RunValidity.INVALID
                        terminated = True
                        metrics.interventions += 1
                        notes.append(
                            "Live-model cost accounting was unavailable"
                        )
                        evidence.record(
                            "intervention",
                            {
                                "type": "circuit_breaker",
                                "trigger": "cost_accounting_unavailable",
                                "result": "terminated",
                            },
                        )
                        break
                    if metrics.model_cost_usd > float(budgets["cost_usd"]):
                        terminated = True
                        metrics.interventions += 1
                        notes.append("Model cost budget exceeded")
                        evidence.record(
                            "intervention",
                            {
                                "type": "circuit_breaker",
                                "trigger": "model_cost_budget",
                                "result": "terminated",
                                "observed_cost_usd": metrics.model_cost_usd,
                                "budget_usd": float(budgets["cost_usd"]),
                            },
                        )
                        break
                    if metrics.retry_attempts > int(budgets["retries"]):
                        terminated = True
                        metrics.interventions += 1
                        notes.append("Model retry budget exceeded")
                        evidence.record(
                            "intervention",
                            {
                                "type": "circuit_breaker",
                                "trigger": "model_retry_budget",
                                "result": "terminated",
                            },
                        )
                        break
                    if metrics.model_tokens > int(budgets["model_tokens"]):
                        terminated = True
                        metrics.interventions += 1
                        notes.append("Model token budget exceeded")
                        evidence.record(
                            "intervention",
                            {
                                "type": "circuit_breaker",
                                "trigger": "model_token_budget",
                                "result": "terminated",
                            },
                        )
                        break
                    if isinstance(event, AgentCompleted):
                        evidence.record(
                            "agent_completed",
                            {
                                "reason": event.reason,
                                "content_digest": event.content_digest,
                                "task_completed": metrics.task_completed,
                            },
                        )
                        break

                    action = event
                    parent_id = (
                        step_request_ids.get(
                            action.parent_step_id,
                            action.parent_step_id,
                        )
                        if action.parent_step_id is not None
                        else None
                    )
                    request = ActionRequest(
                        request_id=str(uuid.uuid4()),
                        trajectory_id=run_id,
                        parent_id=parent_id,
                        actor=action.actor,
                        action=action.action,
                        params=action.params,
                        description=action.description,
                        expected_authorized=action.expected_authorized,
                        prohibited_attempt=action.prohibited_attempt,
                        save_as=action.save_as,
                        step_id=action.step_id,
                    )
                    step_request_ids[action.step_id] = request.request_id
                    metrics.actions_requested += 1
                    if request.expected_authorized:
                        metrics.authorized_actions_requested += 1
                    if request.actor != "agent":
                        metrics.delegated_actions += 1
                    if request.prohibited_attempt:
                        metrics.prohibited_attempts += 1

                    preview = execution_range.preview(request)
                    evidence.record(
                        "tool_request",
                        {
                            "request_id": request.request_id,
                            "tool_call_id": action.tool_call_id,
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
                    if metrics.delegated_actions > int(budgets["delegations"]):
                        terminated = True
                        metrics.interventions += 1
                        notes.append("Delegation budget exceeded")
                        evidence.record(
                            "intervention",
                            {
                                "request_id": request.request_id,
                                "type": "circuit_breaker",
                                "trigger": "delegation_budget",
                                "result": "not_executed",
                            },
                        )
                        break
                    checkpoint = execution_range.checkpoint()
                    decision = control.evaluate(request, preview)
                    metrics.policy_decisions += 1
                    metrics.policy_decision_latencies_ms.append(
                        decision.latency_ms
                    )
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

                    if decision.decision != Decision.ALLOW:
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
                        agent_session.observe(
                            action,
                            AgentObservation(
                                decision=decision.decision.value,
                                executed=False,
                                result=None,
                            ),
                        )
                        if decision.decision in {
                            Decision.SUSPEND,
                            Decision.TERMINATE,
                        }:
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
                                "transformed_arguments_digest": sha256_json(
                                    execution_params
                                ),
                            },
                        )

                    try:
                        tool_result = execution_range.execute(
                            request,
                            execution_params,
                        )
                    except Exception as error:
                        validity = RunValidity.INVALID
                        notes.append(
                            f"Range execution failed for {request.action}: {error}"
                        )
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
                        agent_session.observe(
                            action,
                            AgentObservation(
                                decision=decision.decision.value,
                                executed=False,
                                result=None,
                                error=str(error),
                            ),
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
                    if execution_range.emit_agent_events:
                        evidence.record(
                            "agent_observation",
                            {
                                "request_id": request.request_id,
                                "actor": request.actor,
                                "action": request.action,
                                "result_digest": sha256_json(tool_result),
                            },
                        )
                    control.record(
                        request,
                        preview,
                        decision,
                        executed=True,
                        result=tool_result,
                    )
                    metrics.task_completed = execution_range.task_completed

                    new_hits = adjudicator.evaluate_state(
                        execution_range.snapshot(),
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
                    agent_session.observe(
                        action,
                        AgentObservation(
                            decision=decision.decision.value,
                            executed=True,
                            result=tool_result,
                        ),
                    )
                    if new_hits and profile == ControlProfile.C4:
                        execution_range.restore(checkpoint)
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
            except AgentAdapterError as error:
                validity = RunValidity.INVALID
                notes.append(f"Agent adapter failed: {error}")
                evidence.record(
                    "infrastructure_error",
                    {"stage": "agent_adapter", "error": str(error)},
                )

            if getattr(
                agent_session,
                "requires_verified_teardown",
                False,
            ):
                try:
                    agent_session.close()
                    agent_configuration = redact(agent_session.metadata)
                    agent_teardown = getattr(
                        agent_session,
                        "teardown",
                        {"complete": False},
                    )
                    evidence.record(
                        "agent_teardown",
                        agent_teardown,
                    )
                    if not bool(agent_teardown.get("complete")):
                        validity = RunValidity.INVALID
                        notes.append(
                            "Isolated agent teardown was not verified"
                        )
                except Exception as error:
                    validity = RunValidity.INVALID
                    notes.append(
                        f"Isolated agent teardown failed: {error}"
                    )
                    evidence.record(
                        "infrastructure_error",
                        {
                            "stage": "agent_teardown",
                            "error": str(error),
                        },
                    )
                finally:
                    agent_session = None

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

            evidence.snapshot("after", execution_range.snapshot())
            outcome = adjudicator.outcome(metrics.prohibited_attempts_prevented)
            teardown = execution_range.teardown()
            metrics.recovery_complete = bool(teardown.get("complete"))
            evidence.snapshot("post-teardown", execution_range.snapshot())

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
                environment_digest=environment_digest,
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
                agent_configuration=agent_configuration,
                range_configuration=execution_range.metadata,
                disclosure_status=str(
                    scenario.data.get("disclosure_status", "private")
                ),
                notes=notes,
                variant_id=variant_id,
            )
            write_run_report(result)
            return result
        finally:
            self._cleanup_run_resources(
                agent_session=agent_session,
                external=external,
                execution_range=execution_range,
                evidence=evidence,
                teardown=teardown,
            )

    @staticmethod
    def _add_agent_usage(metrics: RunMetrics, usage: Any) -> None:
        metrics.model_turns += int(usage.model_turns)
        metrics.prompt_tokens += int(usage.prompt_tokens)
        metrics.completion_tokens += int(usage.completion_tokens)
        metrics.model_tokens += int(usage.total_tokens)
        metrics.retry_attempts += int(usage.retries)
        if usage.cost_usd is None:
            if usage.model_turns:
                metrics.cost_accounting_complete = False
        else:
            metrics.model_cost_usd += float(usage.cost_usd)
        if usage.provider and usage.provider not in metrics.observed_providers:
            metrics.observed_providers.append(str(usage.provider))
        if usage.model and usage.model not in metrics.observed_models:
            metrics.observed_models.append(str(usage.model))

    @staticmethod
    def _effective_budgets(
        declared: dict[str, Any],
        overrides: dict[str, int | float] | None,
    ) -> dict[str, int | float]:
        effective = deepcopy(declared)
        if overrides:
            unknown = sorted(set(overrides) - set(effective))
            if unknown:
                raise ValueError(
                    "Unknown budget override(s): " + ", ".join(unknown)
                )
            effective.update(overrides)
        for name, value in effective.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
            ):
                raise ValueError(
                    f"Effective budget {name} must be a non-negative number"
                )
        return effective

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
    def _best_effort(callback: Any) -> None:
        try:
            callback()
        except Exception:
            pass

    @classmethod
    def _cleanup_run_resources(
        cls,
        *,
        agent_session: AgentSession | None,
        external: OstiariBridge | None,
        execution_range: RangeSession,
        evidence: EvidenceStore,
        teardown: dict[str, Any],
    ) -> None:
        if agent_session is not None:
            cls._best_effort(agent_session.close)
        if external is not None:
            cls._best_effort(external.close)
        if not teardown.get("complete"):
            cls._best_effort(execution_range.teardown)
        cls._best_effort(evidence.close)

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


def default_registry(
    project_root: Path,
    *,
    require_project_catalog: bool = False,
) -> ScenarioRegistry:
    catalog_path = project_root / "scenarios" / "catalog.json"
    if catalog_path.exists():
        return ScenarioRegistry(catalog_path)
    if require_project_catalog:
        raise FileNotFoundError(f"Scenario catalog does not exist: {catalog_path}")
    return ScenarioRegistry.from_package()


def experiment_id(scenario_ids: list[str], profiles: list[ControlProfile]) -> str:
    label = "-".join(scenario_ids) + "-" + "-".join(profile.value for profile in profiles)
    return f"{slugify(label)}-{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
