"""Paired control-profile experiments and deterministic replay."""

from __future__ import annotations

import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from escape_lab.models import ControlProfile, RunResult, Scenario
from escape_lab.orchestrator import RunOrchestrator, experiment_id
from escape_lab.reporting import summarize_experiment, write_experiment_report
from escape_lab.util import read_json, utc_now_iso, write_json


def run_experiment(
    orchestrator: RunOrchestrator,
    *,
    scenario_ids: list[str],
    profiles: list[ControlProfile],
    trials: int,
    base_seed: int,
    allow_c0: bool,
    budget_overrides: dict[str, int | float] | None = None,
    scenario_variant_factory: (
        Callable[[Scenario, int], tuple[Scenario, str]] | None
    ) = None,
    variant_strategy: str | None = None,
) -> tuple[Path, list[RunResult], dict[str, Any]]:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    results: list[RunResult] = []
    for trial in range(trials):
        seed = base_seed + trial
        profile_order = list(profiles)
        random.Random(seed).shuffle(profile_order)
        for scenario_id in scenario_ids:
            scenario_override: Scenario | None = None
            variant_id: str | None = None
            if scenario_variant_factory is not None:
                scenario_override, variant_id = scenario_variant_factory(
                    orchestrator.registry.get(scenario_id),
                    seed,
                )
            for profile in profile_order:
                results.append(
                    orchestrator.run(
                        scenario_id,
                        profile=profile,
                        seed=seed,
                        allow_c0=allow_c0,
                        budget_overrides=budget_overrides,
                        scenario_override=scenario_override,
                        variant_id=variant_id,
                    )
                )

    summary = summarize_experiment(results)
    output_dir = (
        orchestrator.artifacts_root
        / "experiments"
        / experiment_id(scenario_ids, profiles)
    )
    metadata = {
        "created_at": utc_now_iso(),
        "scenario_ids": scenario_ids,
        "profiles": [profile.value for profile in profiles],
        "trials_per_scenario_profile": trials,
        "base_seed": base_seed,
        "paired": True,
        "profile_order_randomized": True,
        "control_backend": orchestrator.control_backend,
        "budget_overrides": budget_overrides or {},
        "variant_strategy": variant_strategy,
    }
    write_experiment_report(
        output_dir,
        metadata=metadata,
        summary=summary,
        results=results,
    )
    return output_dir, results, summary


def replay_run(
    orchestrator: RunOrchestrator,
    run_dir: Path,
    *,
    allow_version_drift: bool = False,
) -> tuple[RunResult, dict[str, Any]]:
    manifest = read_json(run_dir / "run-manifest.json")
    scenario_data = manifest["scenario"]
    scenario_id = str(scenario_data["identity"]["id"])
    registered = orchestrator.registry.get(scenario_id, str(scenario_data["identity"]["version"]))
    digest_match = registered.digest == manifest["scenario_digest"]
    variant_id = manifest.get("variant_id")
    scenario_override: Scenario | None = None
    if variant_id is not None:
        scenario_override = Scenario(
            data=dict(scenario_data),
            digest=str(manifest["scenario_digest"]),
            source=run_dir / "run-manifest.json",
        )
        digest_match = True
    if not digest_match and not allow_version_drift:
        raise ValueError(
            "Registered scenario digest differs from the frozen run. "
            "Use --allow-version-drift only for an explicit compatibility check."
        )
    profile = ControlProfile.parse(str(manifest["control_profile"]))
    result = orchestrator.run(
        scenario_id,
        profile=profile,
        seed=int(manifest["seed"]),
        allow_c0=profile == ControlProfile.C0,
        replay_of=str(manifest["run_id"]),
        budget_overrides=dict(manifest.get("budget_overrides", {})),
        scenario_override=scenario_override,
        variant_id=str(variant_id) if variant_id is not None else None,
    )
    original_result = read_json(run_dir / "result.json")
    original_assertions = sorted(
        hit["assertion_id"] for hit in original_result.get("assertion_hits", [])
    )
    replay_assertions = sorted(hit.assertion_id for hit in result.assertion_hits)
    check = {
        "original_run_id": manifest["run_id"],
        "replay_run_id": result.run_id,
        "scenario_digest_match": digest_match,
        "environment_digest_match": (
            result.environment_digest == manifest["environment_digest"]
        ),
        "outcome_match": result.outcome.value == original_result["outcome"],
        "assertion_set_match": replay_assertions == original_assertions,
        "original_outcome": original_result["outcome"],
        "replay_outcome": result.outcome.value,
        "original_assertions": original_assertions,
        "replay_assertions": replay_assertions,
    }
    check["reproducible"] = all(
        check[field]
        for field in (
            "scenario_digest_match",
            "environment_digest_match",
            "outcome_match",
            "assertion_set_match",
        )
    )
    write_json(result.artifact_dir / "replay-check.json", check)
    return result, check
