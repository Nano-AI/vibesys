# Phase 1 results — {engine × query × metric}

Accuracy is graded vs the DuckDB oracle (symmetric, per-snapshot); throughput +
latency from benchmark.py. The reference maintainer is exact by construction (it
self-tests the harness) and stands in for the bespoke engine until Phase 3. Flink
and RisingWave rows fill in once their infra is up (baselines/*/README.md).

| engine | query | exact | F1 | val MAE | throughput (eps) | p50 (ms) | p99 (ms) |
|---|---|---|---|---|---|---|---|
| python-ref (bespoke stand-in) | metering | 1.0000 | 1.0000 | 0.00 | 609,286 | 0.035 | 0.054 |
| python-ref (bespoke stand-in) | active_users | 1.0000 | 1.0000 | 0.00 | 641,989 | 0.033 | 0.053 |
| python-ref (bespoke stand-in) | top_cost | 1.0000 | 1.0000 | 0.00 | 569,123 | 0.037 | 0.058 |
| python-ref (bespoke stand-in) | stalled | 1.0000 | 1.0000 | 0.00 | 152,575 | 0.146 | 0.161 |
| flink | metering | — | — | — | — | — | — |
| flink | active_users | — | — | — | — | — | — |
| flink | top_cost | — | — | — | — | — | — |
| flink | stalled | — | — | — | — | — | — |
| risingwave | metering | 1.0000 | 1.0000 | 0.00 | — | — | — |
| risingwave | active_users | 1.0000 | 1.0000 | 0.00 | — | — | — |
| risingwave | top_cost | 0.9934 | 1.0000 | 12903.65 | — | — | — |
| risingwave | stalled | 0.0465 | 0.0465 | 0.00 | — | — | — |

Latency here is in-process per-snapshot COMPUTE time; end-to-end Kafka latency
for the live engines is marker-based (CONTRACT.md §7).
