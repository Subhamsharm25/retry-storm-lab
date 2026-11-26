#!/usr/bin/env python3
"""Regenerate every result in the README (tables, plots, results/summary.json).

    python experiments/run_all.py                 # full study, 10 seeds, writes results/
    python experiments/run_all.py --quick --out reports/quick   # 2 seeds, smoke test

E1  main brownout: 6 strategies, 10 s brownout at 20% capacity
E2  brownout-duration sweep: when does each strategy fail to recover?
E3  server-side ablation: FIFO vs FIFO+deadline drop vs LIFO+deadline drop
E4  retry-budget ratio sweep: success rate vs recovery time
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retrylab import STRATEGIES, Scenario, Strategy, simulate  # noqa: E402

DURATIONS = [0, 1, 2, 3, 5, 10, 20]
SERVERS = {
    "fifo": {},
    "fifo+deadline-drop": {"deadline_drop": True},
    "lifo+deadline-drop": {"deadline_drop": True, "lifo": True},
}
RATIOS = [0.0, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0]
# two-sided 95% Student t critical values by degrees of freedom
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def run_one(job: tuple[str, dict, dict, int]) -> tuple[str, dict, list[int], list[int]]:
    key, sc_kw, st_kw, seed = job
    res = simulate(Scenario(**sc_kw), Strategy(**st_kw), seed)
    d = res.summary()
    d["last10_goodput"] = sum(res.goodput[-10:]) / 10
    return key, d, res.goodput, res.attempt_rate


def agg(values: list[float]) -> dict:
    xs = [v for v in values if v is not None and not math.isnan(v)]
    if not xs:
        return {"mean": None, "ci95": None, "n": 0}
    m = statistics.fmean(xs)
    ci = T95.get(len(xs) - 1, 1.96) * statistics.stdev(xs) / math.sqrt(len(xs)) if len(xs) > 1 else 0.0
    return {"mean": round(m, 6), "ci95": round(ci, 6), "n": len(xs)}


def summarise(runs: list[dict]) -> dict:
    out = {k: agg([r[k] for r in runs]) for k in ("success_rate", "amplification", "wasted_fraction", "p99")}
    out["last10_goodput"] = agg([r["last10_goodput"] for r in runs])
    rec = [r["recovery_time"] for r in runs if r["recovery_time"] is not None]
    out["recovered_fraction"] = round(len(rec) / len(runs), 6)
    out["recovery_median"] = statistics.median(rec) if rec else None
    return out


def build_jobs(seeds: int) -> list[tuple[str, dict, dict, int]]:
    jobs = []
    base = Scenario()
    for name, st in STRATEGIES.items():
        for s in range(seeds):
            jobs.append((f"e1|{name}", {}, asdict(st), s))
            for d in DURATIONS:
                sc = {"outage_end": base.outage_start + d}
                jobs.append((f"e2|{name}|{d}", sc, asdict(st), s))
            for srv, kw in SERVERS.items():
                if srv != "fifo":  # fifo == e1
                    jobs.append((f"e3|{srv}|{name}", kw, asdict(st), s))
    for ratio in RATIOS:
        for s in range(seeds):
            jobs.append((f"e4|{ratio}", {"budget_ratio": ratio}, asdict(STRATEGIES["full-jitter+budget"]), s))
    return jobs


def plots(out: Path, summary: dict, series: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sc = Scenario()
    names = list(STRATEGIES)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True)
    for n in names:
        g, a = series[n]
        ax1.plot(range(len(g)), g, label=n, lw=1.4)
        ax2.plot(range(len(a)), a, label=n, lw=1.4)
    for ax in (ax1, ax2):
        ax.axvspan(sc.outage_start, sc.outage_end, color="0.85", zorder=0)
        ax.grid(alpha=0.3)
    ax1.axhline(sc.rate, color="k", ls=":", lw=1)
    ax1.set_ylabel("goodput (successful req/s)")
    ax2.axhline(sc.capacity, color="k", ls=":", lw=1)
    ax2.set_ylabel("attempts sent / s")
    ax2.set_xlabel("time (s) — grey band = brownout at 20% capacity")
    ax1.legend(fontsize=8, ncol=3, loc="lower right")
    ax1.set_title("E1: goodput and offered attempts (mean of seeds, simulated)")
    fig.tight_layout()
    fig.savefig(out / "e1_timeline.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for n in names:
        ys = [summary["e2"][n][str(d)]["recovered_fraction"] for d in DURATIONS]
        ax.plot(DURATIONS, ys, marker="o", label=n)
    ax.set_xlabel("brownout duration (s)")
    ax.set_ylabel("fraction of seeds that recover")
    ax.set_title("E2: recovery after a brownout of growing length (simulated)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out / "e2_recovery.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    w = 0.27
    for i, srv in enumerate(SERVERS):
        ys = [summary["e3"][srv][n]["success_rate"]["mean"] for n in names]
        es = [summary["e3"][srv][n]["success_rate"]["ci95"] for n in names]
        ax.bar([x + (i - 1) * w for x in range(len(names))], ys, w, yerr=es, label=srv, capsize=2)
    ax.set_xticks(range(len(names)), names, rotation=20, fontsize=8)
    ax.set_ylabel("success rate")
    ax.set_ylim(0, 1)
    ax.set_title("E3: client strategy x server queue policy (10 s brownout, simulated)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "e3_server.png", dpi=120)
    plt.close(fig)

    fig, ax1 = plt.subplots(figsize=(8, 4.5))
    xs = RATIOS
    ax1.plot(xs, [summary["e4"][str(r)]["success_rate"]["mean"] for r in xs], "o-", color="C0")
    ax1.set_xscale("symlog", linthresh=0.02)
    ax1.set_xlabel("retry budget ratio (retries per first attempt)")
    ax1.set_ylabel("success rate", color="C0")
    ax2 = ax1.twinx()
    ax2.plot(xs, [summary["e4"][str(r)]["recovered_fraction"] for r in xs], "s--", color="C3")
    ax2.set_ylabel("fraction of seeds that recover", color="C3")
    ax2.set_ylim(-0.05, 1.05)
    ax1.set_title("E4: retry budget size (full jitter, 10 s brownout, simulated)")
    ax1.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "e4_budget.png", dpi=120)
    plt.close(fig)


def fmt(m: dict, pct: bool = False, digits: int = 2) -> str:
    if m["mean"] is None:
        return "–"
    if pct:
        return f"{100 * m['mean']:.1f}% ± {100 * m['ci95']:.1f}"
    return f"{m['mean']:.{digits}f} ± {m['ci95']:.{digits}f}"


def rec(s: dict) -> str:
    med = s["recovery_median"]
    return f"{s['recovered_fraction'] * 100:.0f}% ({med:.0f} s)" if med is not None else "0% (never)"


def tables(summary: dict) -> str:
    lines = ["### E0 — healthy server, no brownout (from E2, duration 0)", ""]
    lines.append("| strategy | success rate | attempts / request | p99 latency (s) |")
    lines.append("|---|---|---|---|")
    for n, row in summary["e2"].items():
        s = row["0"]
        lines.append(f"| {n} | {fmt(s['success_rate'], True)} | {fmt(s['amplification'])} | {fmt(s['p99'])} |")
    lines += ["", "### E1 — 10 s brownout at 20% capacity", ""]
    lines.append(
        "| strategy | success rate | attempts / request | wasted server work | p99 latency (s) "
        "| recovered (median time) | goodput, last 10 s (req/s) |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for n, s in summary["e1"].items():
        lines.append(
            f"| {n} | {fmt(s['success_rate'], True)} | {fmt(s['amplification'])} | {fmt(s['wasted_fraction'], True)} "
            f"| {fmt(s['p99'])} | {rec(s)} | {fmt(s['last10_goodput'], digits=1)} |"
        )
    lines += ["", "### E2 — fraction of seeds that recover, by brownout duration", ""]
    lines.append("| strategy | " + " | ".join(f"{d} s" for d in DURATIONS) + " |")
    lines.append("|---|" + "---|" * len(DURATIONS))
    for n, row in summary["e2"].items():
        lines.append(f"| {n} | " + " | ".join(f"{row[str(d)]['recovered_fraction']:.1f}" for d in DURATIONS) + " |")
    lines += ["", "### E3 — success rate by server queue policy (10 s brownout)", ""]
    lines.append("| strategy | " + " | ".join(SERVERS) + " |")
    lines.append("|---|" + "---|" * len(SERVERS))
    for n in STRATEGIES:
        cells = [f"{fmt(summary['e3'][srv][n]['success_rate'], True)}, {rec(summary['e3'][srv][n])}" for srv in SERVERS]
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    lines += ["", "### E4 — retry budget ratio (full jitter, 10 s brownout)", ""]
    lines.append("| ratio | success rate | attempts / request | recovered (median time) |")
    lines.append("|---|---|---|---|")
    for r, s in summary["e4"].items():
        lines.append(f"| {r} | {fmt(s['success_rate'], True)} | {fmt(s['amplification'])} | {rec(s)} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--quick", action="store_true", help="2 seeds (smoke test)")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args(argv)
    seeds = 2 if args.quick else args.seeds
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    jobs = build_jobs(seeds)
    print(f"running {len(jobs)} simulations on {args.workers} workers ...", flush=True)
    groups: dict[str, list[dict]] = {}
    curves: dict[str, list[tuple[list[int], list[int]]]] = {}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for key, d, g, a in ex.map(run_one, jobs, chunksize=4):
            groups.setdefault(key, []).append(d)
            if key.startswith("e1|"):
                curves.setdefault(key[3:], []).append((g, a))

    summary: dict = {"config": {"scenario": asdict(Scenario()), "seeds": seeds}, "e1": {}, "e2": {}, "e3": {}, "e4": {}}
    for n in STRATEGIES:
        summary["e1"][n] = summarise(groups[f"e1|{n}"])
        summary["e2"][n] = {str(d): summarise(groups[f"e2|{n}|{d}"]) for d in DURATIONS}
    summary["e3"]["fifo"] = summary["e1"]
    for srv in SERVERS:
        if srv != "fifo":
            summary["e3"][srv] = {n: summarise(groups[f"e3|{srv}|{n}"]) for n in STRATEGIES}
    for r in RATIOS:
        summary["e4"][str(r)] = summarise(groups[f"e4|{r}"])

    series = {}
    for n, cs in curves.items():
        g = [statistics.fmean(c[0][t] for c in cs) for t in range(len(cs[0][0]))]
        a = [statistics.fmean(c[1][t] for c in cs) for t in range(len(cs[0][1]))]
        series[n] = (g, a)

    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n")
    (out / "tables.md").write_text(tables(summary), encoding="utf-8", newline="\n")
    plots(out, summary, series)
    print(f"wrote {out / 'summary.json'}, tables.md and 4 plots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
