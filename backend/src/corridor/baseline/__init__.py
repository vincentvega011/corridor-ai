"""The naive baseline agent (docs/spec/architecture.md 4.3, ADR 0010).

This package exists only for the comparison: the evaluation runner and the compare endpoint import it.
No full-mode module may import it (a test enforces this), and it refuses to run against the sandbox
database. See docs/baseline.md for exactly what it has and what it lacks.
"""

from corridor.baseline.agent import BaselineAgent
from corridor.baseline.isolation import BaselineIsolationError, assert_isolated

__all__ = ["BaselineAgent", "BaselineIsolationError", "assert_isolated"]
