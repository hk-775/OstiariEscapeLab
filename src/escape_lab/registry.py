"""Versioned scenario registry and manifest validation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from escape_lab.models import Scenario
from escape_lab.util import deep_merge, read_json, sha256_json

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SCENARIO_ID = re.compile(r"^S(0[1-9]|1[0-2])$")
_FAILURE_MODE = re.compile(r"^E(0[1-9]|1[0-2])$")

_REQUIRED_TOP_LEVEL = {
    "identity",
    "title",
    "failure_mode",
    "tier",
    "task",
    "authority",
    "prohibited_outcomes",
    "environment",
    "stimulus",
    "controls",
    "instrumentation",
    "budgets",
    "adjudication",
    "execution",
}


class ScenarioValidationError(ValueError):
    """Raised when a scenario contract is malformed or ambiguous."""


class ScenarioRegistry:
    def __init__(self, catalog_path: Path) -> None:
        self.catalog_path = catalog_path
        self._scenarios: dict[tuple[str, str], Scenario] = {}
        self._load()

    def _load(self) -> None:
        payload = read_json(self.catalog_path)
        if not isinstance(payload, dict):
            raise ScenarioValidationError("Scenario catalog must be a JSON object")
        defaults = payload.get("defaults", {})
        raw_scenarios = payload.get("scenarios")
        if not isinstance(defaults, dict) or not isinstance(raw_scenarios, list):
            raise ScenarioValidationError("Catalog requires object 'defaults' and list 'scenarios'")

        issues: list[str] = []
        for index, raw in enumerate(raw_scenarios):
            if not isinstance(raw, dict):
                issues.append(f"scenarios[{index}] must be an object")
                continue
            resolved = deep_merge(defaults, raw)
            scenario_issues = self._validate_scenario(resolved)
            prefix = f"scenarios[{index}]"
            issues.extend(f"{prefix}: {issue}" for issue in scenario_issues)
            if scenario_issues:
                continue
            scenario = Scenario(
                data=resolved,
                digest=sha256_json(resolved),
                source=self.catalog_path,
            )
            key = (scenario.scenario_id, scenario.version)
            if key in self._scenarios:
                issues.append(f"{prefix}: duplicate scenario version {key[0]}@{key[1]}")
            else:
                self._scenarios[key] = scenario

        if issues:
            raise ScenarioValidationError("\n".join(issues))

    @staticmethod
    def _validate_scenario(data: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        missing = sorted(_REQUIRED_TOP_LEVEL - set(data))
        if missing:
            issues.append(f"missing required fields: {', '.join(missing)}")
            return issues

        identity = data.get("identity")
        if not isinstance(identity, dict):
            return ["identity must be an object"]
        for field in ("id", "version", "owner", "provenance", "review_status"):
            if not identity.get(field):
                issues.append(f"identity.{field} is required")

        scenario_id = str(identity.get("id", ""))
        version = str(identity.get("version", ""))
        failure_mode = str(data.get("failure_mode", ""))
        if not _SCENARIO_ID.fullmatch(scenario_id):
            issues.append("identity.id must be S01 through S12")
        if not _SEMVER.fullmatch(version):
            issues.append("identity.version must use semantic versioning")
        if not _FAILURE_MODE.fullmatch(failure_mode):
            issues.append("failure_mode must be E01 through E12")
        if scenario_id and failure_mode and scenario_id[1:] != failure_mode[1:]:
            issues.append("scenario ID and failure mode must map one-to-one")
        if data.get("tier") not in {"T0", "T1", "T2", "T3"}:
            issues.append("tier must be T0, T1, T2, or T3")

        for field in ("task", "authority", "environment", "stimulus", "controls"):
            if not isinstance(data.get(field), dict):
                issues.append(f"{field} must be an object")
        for field in ("instrumentation", "budgets", "adjudication", "execution"):
            if not isinstance(data.get(field), dict):
                issues.append(f"{field} must be an object")

        outcomes = data.get("prohibited_outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            issues.append("prohibited_outcomes must be a non-empty list")
        else:
            for index, assertion in enumerate(outcomes):
                if not isinstance(assertion, dict):
                    issues.append(f"prohibited_outcomes[{index}] must be an object")
                    continue
                for field in ("id", "kind", "level", "severity", "description"):
                    if field not in assertion:
                        issues.append(f"prohibited_outcomes[{index}].{field} is required")
                if assertion.get("level") not in {"O2", "O3", "O4"}:
                    issues.append(f"prohibited_outcomes[{index}].level must be O2, O3, or O4")

        execution = data.get("execution", {})
        if execution.get("driver") != "scripted":
            issues.append("MVP execution.driver must be 'scripted'")
        steps = execution.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append("execution.steps must be a non-empty list")
        else:
            seen_step_ids: set[str] = set()
            for index, step in enumerate(steps):
                if not isinstance(step, dict):
                    issues.append(f"execution.steps[{index}] must be an object")
                    continue
                for field in ("id", "actor", "action", "params"):
                    if field not in step:
                        issues.append(f"execution.steps[{index}].{field} is required")
                step_id = str(step.get("id", ""))
                if step_id in seen_step_ids:
                    issues.append(f"execution.steps[{index}].id is duplicated")
                seen_step_ids.add(step_id)

        budgets = data.get("budgets", {})
        for field in (
            "wall_clock_seconds",
            "model_tokens",
            "cost_usd",
            "steps",
            "retries",
            "delegations",
            "tool_invocations",
        ):
            value = budgets.get(field)
            if not isinstance(value, (int, float)) or value < 0:
                issues.append(f"budgets.{field} must be a non-negative number")
        return issues

    def list(self) -> list[Scenario]:
        return sorted(self._scenarios.values(), key=lambda item: (item.scenario_id, item.version))

    def get(self, scenario_id: str, version: str | None = None) -> Scenario:
        normalized_id = scenario_id.upper()
        candidates = [
            scenario
            for (registered_id, registered_version), scenario in self._scenarios.items()
            if registered_id == normalized_id and (version is None or registered_version == version)
        ]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise KeyError(f"Unknown scenario {normalized_id}{suffix}")
        return sorted(
            candidates,
            key=lambda item: tuple(int(part) for part in item.version.split(".")),
        )[-1]

    def validate_mvp(self) -> list[str]:
        issues: list[str] = []
        latest = {scenario.scenario_id: self.get(scenario.scenario_id) for scenario in self.list()}
        expected_ids = {f"S{number:02d}" for number in range(1, 13)}
        actual_ids = set(latest)
        missing = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        if missing:
            issues.append(f"missing MVP scenarios: {', '.join(missing)}")
        if extra:
            issues.append(f"unexpected MVP scenarios: {', '.join(extra)}")
        for number in range(1, 13):
            scenario_id = f"S{number:02d}"
            scenario = latest.get(scenario_id)
            if scenario and scenario.failure_mode != f"E{number:02d}":
                issues.append(
                    f"{scenario_id} maps to {scenario.failure_mode}, expected E{number:02d}"
                )
        t2_count = sum(scenario.tier == "T2" for scenario in latest.values())
        if t2_count < 4:
            issues.append(f"MVP needs at least four T2 scenarios; found {t2_count}")
        return issues
