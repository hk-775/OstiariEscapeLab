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

    def test_readme_embeds_editable_aws_reference_architecture(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        diagram_root = DOCS_ROOT / "diagrams"

        self.assertIn(
            "docs/diagrams/aws-reference-architecture.png",
            readme,
        )
        self.assertIn(
            "docs/diagrams/aws-reference-architecture.drawio",
            readme,
        )
        self.assertIn(
            "current MVP does not provision or certify these AWS resources",
            readme,
        )

        for name in (
            "aws-reference-architecture.png",
            "aws-reference-architecture.drawio",
        ):
            with self.subTest(asset=name):
                path = diagram_root / name
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

    def test_gvisor_fixture_result_records_zero_network_and_identity(
        self,
    ) -> None:
        result = json.loads(
            (
                DOCS_ROOT
                / "results"
                / "axonllm-gvisor-fixture-v0.2.json"
            ).read_text(encoding="utf-8")
        )

        self.assertTrue(result["gate"]["passed"])
        self.assertEqual("gvisor", result["configuration"]["agent_runtime"])
        self.assertEqual("runsc", result["configuration"]["oci_runtime"])
        self.assertFalse(
            result["configuration"]["agent_public_network_access"]
        )
        self.assertFalse(result["configuration"]["production_credentials"])
        self.assertTrue(result["attestation"]["container_contract"]["passed"])
        self.assertTrue(result["attestation"]["in_boundary_probe"]["passed"])
        self.assertEqual(3, result["result"]["verified_container_removals"])

    def test_private_pilot_shadow_result_is_clearly_bounded(self) -> None:
        result = json.loads(
            (
                DOCS_ROOT
                / "results"
                / "private-pilot-shadow-v0.1.json"
            ).read_text(encoding="utf-8")
        )
        page = (DOCS_ROOT / "index.html").read_text(encoding="utf-8")

        self.assertEqual("credential-free-shadow", result["result_type"])
        self.assertEqual("awaiting_review", result["status"])
        self.assertTrue(result["execution_passed"])
        self.assertEqual(480, result["benchmark"]["runs"])
        self.assertEqual(0, result["benchmark"]["failed_checks"])
        self.assertEqual(156, result["review_queue"]["entries"])
        self.assertTrue(
            any(
                "does not sample a language model" in limitation
                for limitation in result["limitations"]
            )
        )
        self.assertIn("private-pilot-shadow-v0.1.json", page)

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
