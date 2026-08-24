from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from escape_lab.orchestrator import default_registry
from escape_lab.resources import resource_json

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_packaged_catalog_matches_reviewed_source(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "scenarios" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(source, resource_json("scenarios/catalog.json"))

    def test_packaged_baseline_matches_reviewed_source(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "baselines" / "first-product.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(source, resource_json("baselines/first-product.json"))

    def test_packaged_gvisor_baseline_matches_reviewed_source(self) -> None:
        source = json.loads(
            (PROJECT_ROOT / "baselines" / "gvisor-fixture.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            source,
            resource_json("baselines/gvisor-fixture.json"),
        )

    def test_packaged_external_gvisor_baseline_matches_reviewed_source(
        self,
    ) -> None:
        source = json.loads(
            (PROJECT_ROOT / "baselines" / "gvisor-external.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            source,
            resource_json("baselines/gvisor-external.json"),
        )

    def test_packaged_private_benchmark_plan_matches_reviewed_source(
        self,
    ) -> None:
        source = json.loads(
            (
                PROJECT_ROOT
                / "benchmarks"
                / "private-pilot-v0.1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            source,
            resource_json("benchmarks/private-pilot-v0.1.json"),
        )

    def test_packaged_shadow_benchmark_plan_matches_reviewed_source(
        self,
    ) -> None:
        source = json.loads(
            (
                PROJECT_ROOT
                / "benchmarks"
                / "private-pilot-shadow-v0.1.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            source,
            resource_json(
                "benchmarks/private-pilot-shadow-v0.1.json"
            ),
        )

    def test_packaged_schema_matches_reviewed_source(self) -> None:
        source = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "scenario-manifest.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            source,
            resource_json("schemas/scenario-manifest.schema.json"),
        )

    def test_packaged_benchmark_schema_matches_reviewed_source(self) -> None:
        source = json.loads(
            (
                PROJECT_ROOT
                / "schemas"
                / "benchmark-plan.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            source,
            resource_json("schemas/benchmark-plan.schema.json"),
        )

    def test_registry_loads_without_a_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = default_registry(Path(temporary))
        self.assertEqual(12, len(registry.list()))
        self.assertEqual([], registry.validate_mvp())


if __name__ == "__main__":
    unittest.main()
