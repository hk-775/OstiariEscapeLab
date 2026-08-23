"""Ostiari Escape Lab.

A defensive, provider-neutral agent-containment benchmark. The package's
default runner is deterministic and synthetic: it never executes arbitrary
agent-provided shell commands and never opens network connections.
"""

from escape_lab.models import ControlProfile, Decision, OutcomeLevel, RunResult

__all__ = ["ControlProfile", "Decision", "OutcomeLevel", "RunResult"]
__version__ = "0.2.0"
