from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from escape_lab.registry import ScenarioRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExternalAgentExampleTests(unittest.TestCase):
    def test_example_worker_speaks_agent_rpc(self) -> None:
        scenario = ScenarioRegistry(
            PROJECT_ROOT / "scenarios" / "catalog.json"
        ).get("S06")
        requests = [
            {
                "id": 1,
                "operation": "init",
                "payload": {
                    "scenario": scenario.data,
                    "scenario_digest": scenario.digest,
                    "seed": 17,
                    "run_id": "example-test",
                    "max_turns": 20,
                    "protocol": "ostiari-agent-rpc-v1",
                },
            },
            {"id": 2, "operation": "next_event", "payload": {}},
            {
                "id": 3,
                "operation": "observe",
                "payload": {
                    "action": {"save_as": "customer_record"},
                    "observation": {
                        "decision": "allow",
                        "executed": True,
                        "result": {"content": "synthetic-record"},
                    },
                },
            },
            {"id": 4, "operation": "next_event", "payload": {}},
            {"id": 5, "operation": "shutdown", "payload": {}},
        ]
        encoded = "".join(
            json.dumps(request, sort_keys=True) + "\n"
            for request in requests
        )
        process = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "examples" / "agent-rpc" / "worker.py"),
            ],
            input=encoded,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(0, process.returncode, process.stderr)
        responses = [
            json.loads(line) for line in process.stdout.splitlines()
        ]
        self.assertEqual(5, len(responses))
        self.assertTrue(all(response["ok"] for response in responses))
        self.assertEqual(
            "example-agent-rpc",
            responses[0]["result"]["agent_metadata"]["name"],
        )
        self.assertEqual(
            "file.read",
            responses[1]["result"]["event"]["action"],
        )
        self.assertEqual(
            {"content": "synthetic-record"},
            responses[3]["result"]["event"]["params"]["payload"],
        )


if __name__ == "__main__":
    unittest.main()
