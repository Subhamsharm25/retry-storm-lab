"""retry-storm-lab: a discrete-event study of client retry strategies under a server brownout."""

from .policies import STRATEGIES, RetryBudget, Strategy, backoff_delay
from .sim import Result, Scenario, simulate

__all__ = ["STRATEGIES", "Result", "RetryBudget", "Scenario", "Strategy", "backoff_delay", "simulate"]
__version__ = "0.1.0"
