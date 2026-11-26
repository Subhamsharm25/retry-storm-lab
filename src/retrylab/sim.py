"""Discrete-event simulation of open-loop clients retrying against a bounded-queue server.

Model (all simulated, nothing is measured on real hardware):

* Logical requests arrive as a Poisson process (open loop: arrivals do not wait for responses).
* The server has ``workers`` parallel workers, a FIFO queue of ``queue_limit`` slots and exponential
  service times. A full queue rejects an attempt immediately (fast failure).
* Every completed attempt independently fails with probability ``error_rate`` (transient errors,
  the case retries exist for).
* Brownout: attempts that *start* service during [outage_start, outage_end) take
  ``outage_slowdown`` times longer.
* The client gives up on an attempt after ``timeout`` seconds. The server does not know that,
  so it keeps working on abandoned attempts (wasted work) unless ``deadline_drop`` is set, in which
  case a worker discards an attempt whose deadline has passed when it reaches the head of the queue.
  ``lifo`` serves the newest queued attempt first instead of the oldest.
* After a failed attempt (error, rejection or timeout) the client's strategy decides whether to
  retry and how long to wait. At most ``max_attempts`` attempts per logical request.

Three independent random streams (arrivals, service, policy jitter) keep arrivals identical across
strategies for the same seed (common random numbers), which makes paired comparisons fairer.
"""

from __future__ import annotations

import heapq
import itertools
import math
import random
from collections import deque
from dataclasses import asdict, dataclass, field

from .policies import RetryBudget, Strategy

ARRIVAL, RECV, DONE, RESP, TIMEOUT, RETRY = range(6)


@dataclass(frozen=True)
class Scenario:
    rate: float = 80.0  # logical requests per second
    workers: int = 10
    service_mean: float = 0.1  # seconds -> healthy capacity = workers / service_mean = 100 req/s
    queue_limit: int = 200
    timeout: float = 1.0  # client per-attempt timeout (s)
    max_attempts: int = 4  # first attempt + 3 retries
    error_rate: float = 0.05
    horizon: float = 90.0  # arrivals stop here; the run drains afterwards
    outage_start: float = 20.0
    outage_end: float = 30.0
    outage_slowdown: float = 5.0
    rtt: float = 0.002
    deadline_drop: bool = False
    lifo: bool = False  # serve the newest queued attempt first (default FIFO)
    budget_ratio: float = 0.1
    budget_cap: float = 10.0
    recovery_window: int = 5  # seconds
    recovery_fraction: float = 0.85  # of offered rate

    @property
    def capacity(self) -> float:
        return self.workers / self.service_mean


@dataclass
class Result:
    strategy: str
    seed: int
    requests: int
    succeeded: int
    attempts: int
    rejected: int
    dropped: int
    busy_time: float
    wasted_time: float
    latencies: list[float] = field(repr=False)
    goodput: list[int] = field(repr=False)  # successful responses per 1 s bin, [0, horizon)
    attempt_rate: list[int] = field(repr=False)  # attempts sent per 1 s bin
    recovery_time: float | None = None  # s after outage_end, None = not within horizon

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.requests if self.requests else 0.0

    @property
    def amplification(self) -> float:
        return self.attempts / self.requests if self.requests else 0.0

    @property
    def wasted_fraction(self) -> float:
        return self.wasted_time / self.busy_time if self.busy_time else 0.0

    def latency_quantile(self, q: float) -> float:
        if not self.latencies:
            return math.nan
        xs = sorted(self.latencies)
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    def summary(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k not in ("latencies", "goodput", "attempt_rate")}
        d.update(
            success_rate=self.success_rate,
            amplification=self.amplification,
            wasted_fraction=self.wasted_fraction,
            p50=self.latency_quantile(0.5),
            p99=self.latency_quantile(0.99),
        )
        return d


def recovery_time(goodput: list[int], sc: Scenario) -> float | None:
    """First t >= outage_end whose ``recovery_window``-second mean goodput reaches the target."""
    w, target = sc.recovery_window, sc.recovery_fraction * sc.rate
    start = int(math.ceil(sc.outage_end))
    for t in range(start, len(goodput) - w + 1):
        if sum(goodput[t : t + w]) / w >= target:
            return float(t - sc.outage_end)
    return None


def simulate(sc: Scenario, st: Strategy, seed: int) -> Result:
    arr_rng = random.Random(seed * 1_000 + 1)
    svc_rng = random.Random(seed * 1_000 + 2)
    pol_rng = random.Random(seed * 1_000 + 3)
    budget = RetryBudget(sc.budget_ratio, sc.budget_cap) if st.budget else None

    events: list = []
    seq = itertools.count()

    def push(t: float, kind: int, x) -> None:
        heapq.heappush(events, (t, next(seq), kind, x))

    r_start: list[float] = []
    r_tries: list[int] = []
    r_prev: list[float] = []
    a_req: list[int] = []
    a_sent: list[float] = []
    a_open: list[bool] = []
    queue: deque[int] = deque()
    nbins = int(math.ceil(sc.horizon))
    goodput = [0] * nbins
    attempt_rate = [0] * nbins
    latencies: list[float] = []
    stats = {"busy": 0, "succeeded": 0, "rejected": 0, "dropped": 0, "busy_time": 0.0, "wasted": 0.0}

    def send(r: int, now: float) -> None:
        a = len(a_req)
        a_req.append(r)
        a_sent.append(now)
        a_open.append(True)
        r_tries[r] += 1
        if now < nbins:
            attempt_rate[int(now)] += 1
        push(now + sc.rtt / 2, RECV, a)
        push(now + sc.timeout, TIMEOUT, a)

    def start_service(now: float) -> None:
        while queue and stats["busy"] < sc.workers:
            a = queue.pop() if sc.lifo else queue.popleft()
            if sc.deadline_drop and now - a_sent[a] >= sc.timeout:
                stats["dropped"] += 1
                continue
            s = svc_rng.expovariate(1.0 / sc.service_mean)
            if sc.outage_start <= now < sc.outage_end:
                s *= sc.outage_slowdown
            stats["busy"] += 1
            push(now + s, DONE, (a, s))

    def attempt_failed(r: int, now: float) -> None:
        n = r_tries[r]  # attempts made so far; the next retry is retry number n
        if st.retries and n < sc.max_attempts and (budget is None or budget.try_spend()):
            d = st.delay(n, r_prev[r], pol_rng)
            r_prev[r] = d
            push(now + d, RETRY, r)

    push(arr_rng.expovariate(sc.rate), ARRIVAL, None)
    while events:
        now, _, kind, x = heapq.heappop(events)
        if kind == ARRIVAL:
            if now >= sc.horizon:
                continue
            r = len(r_start)
            r_start.append(now)
            r_tries.append(0)
            r_prev.append(st.base)
            if budget is not None:
                budget.deposit()
            send(r, now)
            push(now + arr_rng.expovariate(sc.rate), ARRIVAL, None)
        elif kind == RECV:
            if len(queue) >= sc.queue_limit:
                stats["rejected"] += 1
                push(now + sc.rtt / 2, RESP, (x, False))
            else:
                queue.append(x)
                start_service(now)
        elif kind == DONE:
            a, s = x
            stats["busy"] -= 1
            stats["busy_time"] += s
            if not a_open[a]:
                stats["wasted"] += s
            ok = svc_rng.random() >= sc.error_rate
            push(now + sc.rtt / 2, RESP, (a, ok))
            start_service(now)
        elif kind == RESP:
            a, ok = x
            if not a_open[a]:
                continue  # client already gave up on this attempt
            a_open[a] = False
            r = a_req[a]
            if ok:
                stats["succeeded"] += 1
                latencies.append(now - r_start[r])
                if now < nbins:
                    goodput[int(now)] += 1
            else:
                attempt_failed(r, now)
        elif kind == TIMEOUT:
            if a_open[x]:
                a_open[x] = False
                attempt_failed(a_req[x], now)
        else:  # RETRY
            send(x, now)

    res = Result(
        strategy=st.name,
        seed=seed,
        requests=len(r_start),
        succeeded=stats["succeeded"],
        attempts=len(a_req),
        rejected=stats["rejected"],
        dropped=stats["dropped"],
        busy_time=stats["busy_time"],
        wasted_time=stats["wasted"],
        latencies=latencies,
        goodput=goodput,
        attempt_rate=attempt_rate,
    )
    res.recovery_time = recovery_time(goodput, sc)
    return res
