from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from escape_lab.cli import main

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_init_creates_release_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            self.assertEqual(0, main(["init", str(destination)]))
            baseline = destination / ".escape-lab" / "baseline.json"
            self.assertTrue(baseline.is_file())
            self.assertIn(
                '"required_agent_adapter": "axonllm"',
                baseline.read_text(encoding="utf-8"),
            )

    def test_validate_command(self) -> None:
        self.assertEqual(
            0,
            main(["--project-root", str(PROJECT_ROOT), "validate"]),
        )

    def test_external_agent_accepts_shell_free_oci_command(self) -> None:
        self.assertEqual(
            0,
            main(
                [
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--agent",
                    "external",
                    "--agent-runtime",
                    "gvisor",
                    "--agent-image",
                    "example-agent:test",
                    "--agent-command-json",
                    '["python","/opt/agent/worker.py"]',
                    "validate",
                ]
            ),
        )

    def test_external_agent_fails_closed_without_gvisor(self) -> None:
        with redirect_stderr(io.StringIO()) as stderr:
            exit_code = main(
                [
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--agent",
                    "external",
                    "--agent-image",
                    "example-agent:test",
                    "validate",
                ]
            )

        self.assertEqual(2, exit_code)
        self.assertIn(
            "External agents require --agent-runtime gvisor",
            stderr.getvalue(),
        )

    def test_three_scenario_demo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                0,
                main(
                    [
                        "--project-root",
                        str(PROJECT_ROOT),
                        "--artifacts-root",
                        temporary,
                        "demo",
                    ]
                ),
            )

    def test_private_benchmark_plan_validates_without_execution(self) -> None:
        self.assertEqual(
            0,
            main(
                [
                    "benchmark",
                    "--plan",
                    str(
                        PROJECT_ROOT
                        / "benchmarks"
                        / "private-pilot-v0.1.json"
                    ),
                    "--validate-only",
                ]
            ),
        )

    def test_live_benchmark_requires_explicit_spend_confirmation(self) -> None:
        with redirect_stderr(io.StringIO()) as stderr:
            exit_code = main(
                [
                    "benchmark",
                    "--plan",
                    str(
                        PROJECT_ROOT
                        / "benchmarks"
                        / "private-pilot-v0.1.json"
                    ),
                ]
            )

        self.assertEqual(2, exit_code)
        self.assertIn("--confirm-live-spend", stderr.getvalue())

    def test_gate_returns_regression_exit_code_and_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "gate"
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "--project-root",
                        str(PROJECT_ROOT),
                        "--artifacts-root",
                        str(root / "artifacts"),
                        "gate",
                        "--baseline",
                        str(PROJECT_ROOT / "baselines" / "first-product.json"),
                        "--output-dir",
                        str(output),
                    ]
                )

            self.assertEqual(10, exit_code)
            self.assertTrue((output / "gate.json").is_file())
            self.assertTrue((output / "junit.xml").is_file())


if __name__ == "__main__":
    unittest.main()
