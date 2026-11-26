"""Aggregation and table code in experiments/run_all.py (the full run is the `smoke`/`reproduce` stages)."""

import importlib.util
import math
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_all", Path(__file__).parents[1] / "experiments" / "run_all.py")
run_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_all)


def test_agg_uses_student_t_and_ignores_missing():
    a = run_all.agg([1.0, 2.0, 3.0, None, math.nan])
    assert a["mean"] == 2.0 and a["n"] == 3
    assert abs(a["ci95"] - 4.303 * 1.0 / math.sqrt(3)) < 1e-6
    assert run_all.agg([]) == {"mean": None, "ci95": None, "n": 0}
    assert run_all.agg([5.0])["ci95"] == 0.0


def test_job_grid_covers_every_experiment_cell():
    jobs = run_all.build_jobs(1)
    keys = {j[0] for j in jobs}
    n = len(run_all.STRATEGIES)
    assert sum(k.startswith("e1|") for k in keys) == n
    assert sum(k.startswith("e2|") for k in keys) == n * len(run_all.DURATIONS)
    assert sum(k.startswith("e3|") for k in keys) == n * (len(run_all.SERVERS) - 1)
    assert sum(k.startswith("e4|") for k in keys) == len(run_all.RATIOS)


def test_run_one_and_summarise_and_format():
    key, d, g, a = run_all.run_one(
        ("e1|no-retry", {"horizon": 40.0}, run_all.asdict(run_all.STRATEGIES["no-retry"]), 0)
    )
    assert key == "e1|no-retry" and len(g) == len(a) == 40
    s = run_all.summarise([d, d])
    assert s["recovered_fraction"] == 1.0 and s["success_rate"]["n"] == 2
    assert "%" in run_all.fmt(s["success_rate"], pct=True)
    assert run_all.fmt({"mean": None}) == "–"
    assert run_all.rec({"recovered_fraction": 0.0, "recovery_median": None}) == "0% (never)"
