import math
from dataclasses import replace

import pytest

from retrylab import STRATEGIES, Scenario, simulate
from retrylab.sim import Result, recovery_time

SHORT = Scenario(horizon=40.0, outage_start=10.0, outage_end=15.0)
HEALTHY = replace(SHORT, outage_end=SHORT.outage_start)  # zero-length brownout


def test_same_seed_same_result_different_seed_different_result():
    a = simulate(SHORT, STRATEGIES["full-jitter"], 3).summary()
    b = simulate(SHORT, STRATEGIES["full-jitter"], 3).summary()
    c = simulate(SHORT, STRATEGIES["full-jitter"], 4).summary()
    assert a == b and a != c


def test_common_random_numbers_give_identical_arrivals_across_strategies():
    reqs = {simulate(SHORT, s, 7).requests for s in STRATEGIES.values()}
    assert len(reqs) == 1


def test_healthy_no_retry_matches_the_error_rate_and_utilisation():
    sc = replace(HEALTHY, horizon=200.0)
    r = simulate(sc, STRATEGIES["no-retry"], 1)
    assert r.amplification == 1.0
    assert abs(r.success_rate - (1 - sc.error_rate)) < 0.01
    # offered utilisation rho = rate * service_mean / workers = 0.8
    assert abs(r.busy_time / (sc.horizon * sc.workers) - 0.8) < 0.03
    assert r.rejected == 0 and r.wasted_fraction < 0.01
    assert r.recovery_time == 0.0


def test_retries_mask_transient_errors_when_healthy():
    r = simulate(HEALTHY, STRATEGIES["full-jitter"], 1)
    assert r.success_rate > 0.999
    assert 1.0 < r.amplification < 1.1


def test_invariants_hold_for_every_strategy():
    for s in STRATEGIES.values():
        r = simulate(SHORT, s, 2)
        assert 0 <= r.succeeded <= r.requests
        assert r.requests <= r.attempts <= SHORT.max_attempts * r.requests
        assert 0.0 <= r.wasted_time <= r.busy_time
        assert len(r.latencies) == r.succeeded
        assert sum(r.goodput) <= r.succeeded


def test_unbudgeted_retries_go_metastable_and_a_budget_recovers():
    storm = simulate(SHORT, STRATEGIES["immediate"], 1)
    budget = simulate(SHORT, STRATEGIES["full-jitter+budget"], 1)
    assert storm.recovery_time is None and sum(storm.goodput[-5:]) == 0
    assert storm.rejected > 0 and storm.wasted_fraction > 0.5
    assert budget.recovery_time is not None and budget.amplification < 1.2


def test_server_queue_policies():
    dd = simulate(replace(SHORT, deadline_drop=True), STRATEGIES["immediate"], 1)
    lifo = simulate(replace(SHORT, deadline_drop=True, lifo=True), STRATEGIES["immediate"], 1)
    assert dd.dropped > 0
    assert lifo.recovery_time is not None and lifo.success_rate > dd.success_rate


def test_recovery_time_helper():
    sc = Scenario(rate=10.0, outage_end=2.0, recovery_window=2, recovery_fraction=0.5)
    assert recovery_time([10, 0, 0, 0, 5, 5, 9], sc) == 2.0
    assert recovery_time([10, 0, 0, 0, 0, 0, 9], sc) is None


def test_result_edge_cases():
    r = Result("x", 0, 0, 0, 0, 0, 0, 0.0, 0.0, [], [], [])
    assert r.success_rate == r.amplification == r.wasted_fraction == 0.0
    assert math.isnan(r.latency_quantile(0.99))
    assert Scenario().capacity == pytest.approx(100.0)
    s = simulate(HEALTHY, STRATEGIES["no-retry"], 0).summary()
    assert {"success_rate", "amplification", "p99", "recovery_time"} <= set(s) and "latencies" not in s
