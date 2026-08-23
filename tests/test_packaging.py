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

    def test_registry_loads_without_a_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry = default_registry(Path(temporary))
        self.assertEqual(12, len(registry.list()))
        self.assertEqual([], registry.validate_mvp())


if __name__ == "__main__":
    unittest.main()
