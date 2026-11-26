"""Client retry strategies: backoff schedules and a token-bucket retry budget.

Delays follow the definitions popularised by the AWS Architecture Blog post
"Exponential Backoff And Jitter" (Brooker, 2015):

* ``exp``          d_n = min(cap, base * 2**(n-1))
* ``full``         d_n = U(0, min(cap, base * 2**(n-1)))
* ``decorrelated`` d_n = min(cap, U(base, 3 * d_{n-1})), d_0 = base

``n`` is the retry number (1 for the first retry). ``immediate`` retries with zero delay.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

BACKOFFS = ("none", "immediate", "exp", "full", "decorrelated")


def backoff_delay(kind: str, n: int, prev: float, rng: random.Random, base: float = 0.1, cap: float = 5.0) -> float:
    """Delay before retry number ``n`` (n >= 1). ``prev`` is the previous delay (decorrelated only)."""
    if n < 1:
        raise ValueError("retry number starts at 1")
    if kind == "immediate":
        return 0.0
    if kind == "exp":
        return min(cap, base * 2 ** (n - 1))
    if kind == "full":
        return rng.uniform(0.0, min(cap, base * 2 ** (n - 1)))
    if kind == "decorrelated":
        return min(cap, rng.uniform(base, max(base, prev) * 3))
    raise ValueError(f"no delay for backoff kind {kind!r}")


class RetryBudget:
    """Token bucket that caps retries at roughly ``ratio`` of first attempts (plus a small burst).

    Every first attempt deposits ``ratio`` tokens (up to ``cap``); a retry is allowed only if it can
    spend one whole token. This is the idea behind Finagle's RetryBudget and gRPC retry throttling:
    under sustained failure the retry rate is bounded by ``ratio * request rate``.
    """

    def __init__(self, ratio: float = 0.1, cap: float = 10.0) -> None:
        if ratio < 0 or cap < 1:
            raise ValueError("ratio must be >= 0 and cap >= 1")
        self.ratio = ratio
        self.cap = cap
        self.tokens = cap

    def deposit(self) -> None:
        self.tokens = min(self.cap, self.tokens + self.ratio)

    def try_spend(self) -> bool:
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass(frozen=True)
class Strategy:
    name: str
    backoff: str  # one of BACKOFFS
    budget: bool = False
    base: float = 0.1
    cap: float = 5.0

    def __post_init__(self) -> None:
        if self.backoff not in BACKOFFS:
            raise ValueError(f"unknown backoff {self.backoff!r}")

    @property
    def retries(self) -> bool:
        return self.backoff != "none"

    def delay(self, n: int, prev: float, rng: random.Random) -> float:
        return backoff_delay(self.backoff, n, prev, rng, self.base, self.cap)


STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in (
        Strategy("no-retry", "none"),
        Strategy("immediate", "immediate"),
        Strategy("exp-backoff", "exp"),
        Strategy("full-jitter", "full"),
        Strategy("decorrelated-jitter", "decorrelated"),
        Strategy("full-jitter+budget", "full", budget=True),
    )
}
