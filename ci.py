#!/usr/bin/env python3
"""Single source of truth for the pipeline (Jenkinsfile, GitHub Actions and laptops run these).

python ci.py lint        ruff lint + format check
python ci.py unit        unit + property tests (hypothesis), coverage >= 90%
python ci.py smoke       experiments/run_all.py --quick into reports/quick: every output is produced
python ci.py reproduce   full study into reports/repro; must match committed results/summary.json within tolerance
python ci.py claims      every claim the README makes is asserted against results/summary.json (and the
                         reproduced run, if present); README tables must equal results/tables.md
python ci.py all         everything above, in order
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
REPORTS = ROOT / "reports"
RESULTS = ROOT / "results"
OUTPUTS = ["summary.json", "tables.md", "e1_timeline.png", "e2_recovery.png", "e3_server.png", "e4_budget.png"]
UNBUDGETED = ["immediate", "exp-backoff", "full-jitter", "decorrelated-jitter"]


def run(*cmd: str) -> None:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=ROOT)
    if res.returncode != 0:
        raise SystemExit(f"command failed ({res.returncode}): {' '.join(cmd)}")


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit(f"FAILED: {msg}")
    print(f"  ok  {msg}")


# ------------------------------------------------------------------ stages
def lint() -> None:
    run(PY, "-m", "ruff", "check", "src", "tests", "experiments", "ci.py")
    run(PY, "-m", "ruff", "format", "--check", "src", "tests", "experiments", "ci.py")


def unit() -> None:
    run(
        PY,
        "-m",
        "pytest",
        "-q",
        "--cov=retrylab",
        "--cov-report=term-missing",
        "--cov-fail-under=90",
        "--junitxml=reports/unit.xml",
    )


def smoke() -> None:
    out = REPORTS / "quick"
    run(PY, "experiments/run_all.py", "--quick", "--out", str(out))
    for f in OUTPUTS:
        check((out / f).stat().st_size > 0, f"quick run wrote {f}")


def reproduce() -> None:
    out = REPORTS / "repro"
    run(PY, "experiments/run_all.py", "--out", str(out))
    ref = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    new = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    check(ref["config"] == new["config"], "same scenario and seed count as the committed results")
    worst = 0.0
    for exp in ("e1", "e2", "e3", "e4"):
        for path, a, b in _cells(ref[exp], new[exp], exp):
            if path.endswith("recovery_median"):
                ok = (a is None) == (b is None) and (a is None or abs(a - b) <= 2.0)
            elif path.endswith("amplification"):
                ok = abs(a - b) <= 0.02 * max(1.0, abs(a))
            else:
                ok = abs(a - b) <= 0.02
                worst = max(worst, abs(a - b))
            if not ok:
                raise SystemExit(f"FAILED: {path} drifted: committed {a}, reproduced {b}")
    print(f"  ok  reproduced run matches committed results (largest rate difference {worst:.4f})")


def _cells(a: dict, b: dict, path: str):
    """Yield the comparable numbers (means and fractions; p99 is too noisy to pin) from two summaries."""
    for k, va in a.items():
        vb = b[k]
        p = f"{path}.{k}"
        if isinstance(va, dict) and "mean" in va:
            if k in ("success_rate", "amplification", "wasted_fraction"):
                yield p, va["mean"], vb["mean"]
        elif isinstance(va, dict):
            yield from _cells(va, vb, p)
        elif k in ("recovered_fraction", "recovery_median"):
            yield p, va, vb


def _claims(s: dict, label: str) -> None:
    print(f"\nclaims on {label}:")
    e0 = {n: r["0"] for n, r in s["e2"].items()}
    check(0.94 <= e0["no-retry"]["success_rate"]["mean"] <= 0.96, "healthy: no-retry success ~= 1 - error rate (95%)")
    check(
        all(e0[n]["success_rate"]["mean"] >= 0.999 for n in e0 if n != "no-retry"),
        "healthy: every retrying strategy succeeds >= 99.9%",
    )
    e1 = s["e1"]
    check(all(e1[n]["recovered_fraction"] == 0.0 for n in UNBUDGETED), "E1: unbudgeted retries never recover")
    check(all(e1[n]["last10_goodput"]["mean"] < 1.0 for n in UNBUDGETED), "E1: their goodput stays ~0 to the end")
    check(
        e1["no-retry"]["recovered_fraction"] == 1.0 and e1["full-jitter+budget"]["recovered_fraction"] == 1.0,
        "E1: no-retry and the retry budget always recover",
    )
    rates = [e1[n]["success_rate"]["mean"] for n in UNBUDGETED]
    check(max(rates) - min(rates) < 0.02, "E1: jitter does not change the outcome (unbudgeted within 2 pp)")
    check(
        e1["full-jitter+budget"]["amplification"]["mean"] <= 1.1
        and all(e1[n]["amplification"]["mean"] >= 3.0 for n in UNBUDGETED),
        "E1: budget keeps attempts/request <= 1.1; unbudgeted >= 3",
    )
    check(
        all(s["e2"][n][str(d)]["recovered_fraction"] == 0.0 for n in UNBUDGETED for d in (2, 3, 5, 10, 20)),
        "E2: unbudgeted retries never recover from brownouts >= 2 s",
    )
    check(
        all(s["e2"]["full-jitter+budget"][d]["recovered_fraction"] == 1.0 for d in s["e2"]["no-retry"]),
        "E2: budget always recovers",
    )
    e3 = s["e3"]
    check(
        all(e3["fifo+deadline-drop"][n]["recovered_fraction"] == 0.0 for n in UNBUDGETED),
        "E3: FIFO deadline drop alone does not end the storm",
    )
    check(
        all(e3["lifo+deadline-drop"][n]["recovered_fraction"] == 1.0 for n in e3["fifo"])
        and all(e3["lifo+deadline-drop"][n]["success_rate"]["mean"] > 0.9 for n in UNBUDGETED),
        "E3: LIFO + deadline drop recovers every strategy (>90% success even without a budget)",
    )
    e4 = s["e4"]
    check(
        all(e4[r]["recovered_fraction"] == 1.0 for r in ("0.0", "0.02", "0.05", "0.1")),
        "E4: ratio <= 0.1 always recovers",
    )
    check(all(e4[r]["recovered_fraction"] == 0.0 for r in ("0.5", "1.0")), "E4: ratio >= 0.5 never recovers")


def claims() -> None:
    _claims(json.loads((RESULTS / "summary.json").read_text(encoding="utf-8")), "committed results/summary.json")
    repro = REPORTS / "repro" / "summary.json"
    if repro.exists():
        _claims(json.loads(repro.read_text(encoding="utf-8")), "reproduced reports/repro/summary.json")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tables = (RESULTS / "tables.md").read_text(encoding="utf-8").strip()
    check(tables in readme, "README result tables are exactly results/tables.md")
    for f in OUTPUTS[2:]:
        check(f"results/{f}" in readme and (RESULTS / f).exists(), f"README shows committed plot results/{f}")


STAGES = {"lint": lint, "unit": unit, "smoke": smoke, "reproduce": reproduce, "claims": claims}


def main(argv: list[str]) -> int:
    names = list(STAGES) if argv in ([], ["all"]) else argv
    unknown = [n for n in names if n not in STAGES]
    if unknown:
        print(f"unknown stage(s) {unknown}; choose from {list(STAGES)} or all")
        return 2
    REPORTS.mkdir(exist_ok=True)
    for n in names:
        t0 = time.monotonic()
        print(f"\n===== stage: {n} =====", flush=True)
        STAGES[n]()
        print(f"===== {n} passed in {time.monotonic() - t0:.1f}s =====", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
