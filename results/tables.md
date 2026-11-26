### E0 — healthy server, no brownout (from E2, duration 0)

| strategy | success rate | attempts / request | p99 latency (s) |
|---|---|---|---|
| no-retry | 95.0% ± 0.2 | 1.00 ± 0.00 | 0.49 ± 0.01 |
| immediate | 100.0% ± 0.0 | 1.05 ± 0.00 | 0.58 ± 0.04 |
| exp-backoff | 100.0% ± 0.0 | 1.05 ± 0.00 | 0.62 ± 0.03 |
| full-jitter | 100.0% ± 0.0 | 1.05 ± 0.00 | 0.60 ± 0.04 |
| decorrelated-jitter | 100.0% ± 0.0 | 1.05 ± 0.00 | 0.69 ± 0.03 |
| full-jitter+budget | 100.0% ± 0.0 | 1.05 ± 0.00 | 0.60 ± 0.04 |

### E1 — 10 s brownout at 20% capacity

| strategy | success rate | attempts / request | wasted server work | p99 latency (s) | recovered (median time) | goodput, last 10 s (req/s) |
|---|---|---|---|---|---|---|
| no-retry | 78.4% ± 1.4 | 1.00 ± 0.00 | 21.7% ± 1.4 | 0.81 ± 0.05 | 100% (6 s) | 76.4 ± 1.8 |
| immediate | 22.2% ± 0.4 | 3.35 ± 0.01 | 80.8% ± 0.4 | 1.28 ± 0.88 | 0% (never) | 0.0 ± 0.0 |
| exp-backoff | 22.4% ± 0.3 | 3.35 ± 0.01 | 80.8% ± 0.4 | 2.85 ± 1.15 | 0% (never) | 0.0 ± 0.0 |
| full-jitter | 22.3% ± 0.4 | 3.35 ± 0.01 | 80.8% ± 0.3 | 2.92 ± 1.04 | 0% (never) | 0.0 ± 0.0 |
| decorrelated-jitter | 22.4% ± 0.4 | 3.35 ± 0.01 | 80.8% ± 0.3 | 3.14 ± 1.23 | 0% (never) | 0.0 ± 0.0 |
| full-jitter+budget | 78.7% ± 3.1 | 1.06 ± 0.00 | 25.8% ± 3.3 | 0.94 ± 0.05 | 100% (9 s) | 80.0 ± 1.9 |

### E2 — fraction of seeds that recover, by brownout duration

| strategy | 0 s | 1 s | 2 s | 3 s | 5 s | 10 s | 20 s |
|---|---|---|---|---|---|---|---|
| no-retry | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| immediate | 1.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| exp-backoff | 1.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| full-jitter | 1.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| decorrelated-jitter | 1.0 | 0.1 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| full-jitter+budget | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

### E3 — success rate by server queue policy (10 s brownout)

| strategy | fifo | fifo+deadline-drop | lifo+deadline-drop |
|---|---|---|---|
| no-retry | 78.4% ± 1.4, 100% (6 s) | 84.5% ± 0.6, 100% (0 s) | 86.3% ± 0.3, 100% (0 s) |
| immediate | 22.2% ± 0.4, 0% (never) | 31.5% ± 0.4, 0% (never) | 91.0% ± 0.3, 100% (0 s) |
| exp-backoff | 22.4% ± 0.3, 0% (never) | 33.5% ± 0.7, 0% (never) | 91.5% ± 0.3, 100% (0 s) |
| full-jitter | 22.3% ± 0.4, 0% (never) | 32.5% ± 0.5, 0% (never) | 91.3% ± 0.2, 100% (0 s) |
| decorrelated-jitter | 22.4% ± 0.4, 0% (never) | 33.4% ± 0.8, 0% (never) | 91.7% ± 0.3, 100% (0 s) |
| full-jitter+budget | 78.7% ± 3.1, 100% (9 s) | 88.6% ± 0.8, 100% (0 s) | 90.7% ± 0.2, 100% (0 s) |

### E4 — retry budget ratio (full jitter, 10 s brownout)

| ratio | success rate | attempts / request | recovered (median time) |
|---|---|---|---|
| 0.0 | 78.7% ± 1.3 | 1.00 ± 0.00 | 100% (6 s) |
| 0.02 | 79.9% ± 1.5 | 1.02 ± 0.00 | 100% (6 s) |
| 0.05 | 80.8% ± 2.0 | 1.05 ± 0.00 | 100% (8 s) |
| 0.1 | 78.7% ± 3.1 | 1.06 ± 0.00 | 100% (9 s) |
| 0.2 | 58.4% ± 14.1 | 1.12 ± 0.02 | 80% (22 s) |
| 0.5 | 22.1% ± 0.3 | 1.40 ± 0.00 | 0% (never) |
| 1.0 | 22.1% ± 0.3 | 1.78 ± 0.00 | 0% (never) |
