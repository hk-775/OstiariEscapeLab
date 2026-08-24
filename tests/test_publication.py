from __future__ import annotations

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"


class _PublicationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.local_assets: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        candidate = attributes.get("src") if tag == "img" else None
        if candidate and "://" not in candidate:
            self.local_assets.append(candidate)


class PublicationTests(unittest.TestCase):
    def test_results_page_contains_every_reviewed_scenario(self) -> None:
        page = (DOCS_ROOT / "index.html").read_text(encoding="utf-8")
        catalog = json.loads(
            (PROJECT_ROOT / "scenarios" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )

        for scenario in catalog["scenarios"]:
            with self.subTest(scenario=scenario["identity"]["id"]):
                self.assertIn(scenario["identity"]["id"], page)
                self.assertIn(scenario["title"], page)

    def test_every_published_image_exists(self) -> None:
        parser = _PublicationParser()
        parser.feed((DOCS_ROOT / "index.html").read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(parser.local_assets), 4)
        for relative in parser.local_assets:
            with self.subTest(asset=relative):
                path = DOCS_ROOT / relative
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)

    def test_incident_result_records_controlled_and_uncontrolled_runs(self) -> None:
        result = json.loads(
            (
                DOCS_ROOT
                / "results"
                / "openai-hf-incident-replay-v0.2.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual("O4", result["baseline"]["outcome"])
        self.assertEqual("O1", result["controlled"]["outcome"])
        self.assertEqual(
            2,
            result["controlled"]["prohibited_attempts_prevented"],
        )
        self.assertTrue(result["gate"]["passed"])

    def test_oci_incident_result_records_isolation_and_teardown(self) -> None:
        result = json.loads(
            (
                DOCS_ROOT
                / "results"
                / "openai-hf-incident-oci-gate-v0.2.json"
            ).read_text(encoding="utf-8")
        )

        self.assertTrue(result["gate"]["passed"])
        self.assertEqual(
            "docker",
            result["configuration"]["range_backend"],
        )
        self.assertTrue(result["isolation_probe"]["passed"])
        self.assertTrue(result["result"]["container_removed"])

    def test_workflow_actions_are_pinned_to_commits(self) -> None:
        workflow_paths = sorted(
            (PROJECT_ROOT / ".github" / "workflows").glob("*.yml")
        )
        workflow_paths.append(PROJECT_ROOT / "action.yml")
        uses_pattern = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            for reference in uses_pattern.findall(text):
                if reference.startswith("./"):
                    continue
                with self.subTest(path=path.name, reference=reference):
                    self.assertRegex(reference, r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
