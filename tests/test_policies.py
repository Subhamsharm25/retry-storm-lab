import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from retrylab import STRATEGIES, RetryBudget, Strategy, backoff_delay

bases = st.floats(min_value=0.001, max_value=1.0)
caps = st.floats(min_value=1.0, max_value=60.0)
retry_no = st.integers(min_value=1, max_value=40)
seeds = st.integers(min_value=0, max_value=2**32 - 1)


@given(retry_no, bases, caps, seeds)
def test_full_jitter_stays_inside_the_exponential_envelope(n, base, cap, seed):
    d = backoff_delay("full", n, base, random.Random(seed), base, cap)
    assert 0.0 <= d <= min(cap, base * 2 ** (n - 1))


@given(st.lists(retry_no, min_size=1, max_size=10), bases, caps, seeds)
def test_decorrelated_jitter_is_bounded_by_base_and_cap(ns, base, cap, seed):
    rng, prev = random.Random(seed), base
    for n in ns:
        prev = backoff_delay("decorrelated", n, prev, rng, base, cap)
        assert base <= prev <= cap


@given(bases, caps)
def test_exponential_is_monotone_and_capped(base, cap):
    ds = [backoff_delay("exp", n, 0, random.Random(0), base, cap) for n in range(1, 30)]
    assert ds == sorted(ds) and max(ds) <= cap and ds[0] == min(base, cap)


def test_immediate_has_no_delay_and_bad_inputs_raise():
    assert backoff_delay("immediate", 3, 0, random.Random(0)) == 0.0
    with pytest.raises(ValueError):
        backoff_delay("full", 0, 0, random.Random(0))
    with pytest.raises(ValueError):
        backoff_delay("none", 1, 0, random.Random(0))
    with pytest.raises(ValueError):
        Strategy("x", "linear")
    with pytest.raises(ValueError):
        RetryBudget(ratio=-1)


@given(
    st.floats(min_value=0.0, max_value=1.0),
    st.integers(min_value=1, max_value=20),
    st.lists(st.booleans(), max_size=500),
)
def test_budget_never_allows_more_than_burst_plus_ratio_times_requests(ratio, cap, ops):
    b = RetryBudget(ratio, cap)
    deposits = spent = 0
    for is_request in ops:
        if is_request:
            b.deposit()
            deposits += 1
        elif b.try_spend():
            spent += 1
        assert 0.0 <= b.tokens <= cap
    assert spent <= cap + ratio * deposits + 1e-9


def test_strategy_catalogue():
    assert not STRATEGIES["no-retry"].retries
    assert all(s.retries for n, s in STRATEGIES.items() if n != "no-retry")
    assert [n for n, s in STRATEGIES.items() if s.budget] == ["full-jitter+budget"]
    assert STRATEGIES["immediate"].delay(1, 0.1, random.Random(0)) == 0.0
