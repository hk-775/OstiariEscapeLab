from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from escape_lab.agents import AxonLLMAgentFactory, AxonLLMConfig
from escape_lab.models import ControlProfile
from escape_lab.orchestrator import RunOrchestrator
from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AXONLLM_SOURCE = os.environ.get("AXONLLM_SRC")


@unittest.skipUnless(
    AXONLLM_SOURCE,
    "Set AXONLLM_SRC to run the loopback AxonLLM integration",
)
class AxonLLMIntegrationTests(unittest.TestCase):
    def test_tool_calls_cross_the_real_axonllm_router(self) -> None:
        assert AXONLLM_SOURCE is not None
        with tempfile.TemporaryDirectory() as temporary:
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=ScenarioRegistry(
                    PROJECT_ROOT / "scenarios" / "catalog.json"
                ),
                artifacts_root=Path(temporary),
                agent_factory=AxonLLMAgentFactory(
                    AxonLLMConfig(
                        source=Path(AXONLLM_SOURCE),
                        mode="fixture",
                    )
                ),
            )
            result = orchestrator.run(
                "S06",
                profile=ControlProfile.C4,
                seed=17,
            )

        self.assertEqual("axonllm", result.agent_configuration["adapter"])
        self.assertEqual("0.4.0", result.agent_configuration["version"])
        self.assertGreater(result.metrics.model_turns, 0)
        self.assertGreater(result.metrics.model_tokens, 0)
        self.assertEqual("O1", result.outcome.value)
        self.assertEqual("valid", result.validity.value)
        self.assertTrue(result.metrics.task_completed)

    def test_offline_fixture_uses_no_loopback_transport_or_auth(self) -> None:
        assert AXONLLM_SOURCE is not None
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch(
                "escape_lab.agents._FixtureServer",
                side_effect=AssertionError("loopback transport was started"),
            ),
        ):
            orchestrator = RunOrchestrator(
                project_root=PROJECT_ROOT,
                registry=ScenarioRegistry(
                    PROJECT_ROOT / "scenarios" / "catalog.json"
                ),
                artifacts_root=Path(temporary),
                agent_factory=AxonLLMAgentFactory(
                    AxonLLMConfig(
                        source=Path(AXONLLM_SOURCE),
                        mode="fixture",
                        fixture_transport="offline",
                    )
                ),
            )
            result = orchestrator.run(
                "S06",
                profile=ControlProfile.C4,
                seed=17,
            )

        self.assertEqual("offline-fixture", result.agent_configuration["provider"])
        self.assertEqual("offline", result.agent_configuration["fixture_transport"])
        self.assertFalse(result.agent_configuration["network_required"])
        self.assertEqual("none", result.agent_configuration["provider_auth"])
        self.assertGreater(result.metrics.model_turns, 0)
        self.assertEqual("O1", result.outcome.value)
        self.assertEqual("valid", result.validity.value)


if __name__ == "__main__":
    unittest.main()
