# TASKS — Bespoke Streaming Engine Benchmark

Working checklist for the 3-phase plan. Cross-referenced with the VibeServe target
contract (`examples/<name>/` = `OBJECTIVE.md` + `reference/` + `accuracy_checker/` +
`benchmark/`; inner loop Implementer→Accuracy Judge→Perf Evaluator). See `DESIGN.md`
for methodology and `README.md` for the current MVP.

**Legend:** ✅ done · ▶ in progress · ☐ todo

## Pinned decisions
- Headline: bespoke synthesized engine beats **generic streaming engines** on
  non-monotonic + time-window workloads.
- Baselines (Phase 1): **Flink** (Apache-2.0, free) + **RisingWave** (Apache-2.0, free).
  Materialize emulator (BSL, free for non-prod) optional later.
- Early metrics: **throughput, latency, per-snapshot accuracy**. Cost deferred.
- **Correctness = measured, symmetric metric** (oracle defines truth; every engine
  scored on deviation, not gated).
- **Engine-neutral workload**; one correct answer (the DuckDB oracle).
- Bespoke engine language: **Rust**, written by the **agent** (we provide scaffolding).
- Compute target: **CPU/native** (no GPU — see §Phase 2). v1 stream is **in-order**.
- Budgets: 4 cores / 8 GB per engine; Kafka single broker, 8 partitions.

## The two "references" (do not conflate)
1. **VibeServe `reference/` = the DuckDB snapshot oracle** (correctness ground truth).
   REQUIRED. Already built (`oracle.py`). The accuracy checker grades every engine
   against it.
2. **Optional hand-written Rust "reference engine"** = a performance baseline / the
   "what's achievable" bar. NOT required, NOT part of the loop. Also used in Phase 2 as
   a plumbing smoke-test engine.

---

## PHASE 1 — Benchmark harness + baselines (NO agent yet)
**Goal:** a runnable, agent-free benchmark: generate an engine-neutral workload, define
the I/O contract, grade any engine's per-snapshot accuracy vs the oracle, and measure
throughput + latency. Produce baseline numbers for Flink + RisingWave (+ the Python
reference maintainer as a harness self-test).
**Done when:** Flink, RisingWave, and the Python maintainer all run through the same
I/O contract and emit {accuracy, throughput, latency} numbers on the same seeded data.

- ✅ 1.1  Deterministic event generator → `events.csv` (`generate.py`).
- ✅ 1.2  DuckDB snapshot oracle = the `reference/` (`oracle.py`).
- ✅ 1.3  Python reference incremental maintainer, exact (`maintainer.py`) — harness self-test.
- ✅ 1.4  Metering query Q1 + snapshot diff loop (`run.py`) — green, 301/301.
- ✅ 1.5  **Freeze the I/O contract** → `CONTRACT.md` (v1): input event record (JSON over
  Kafka `EVENTS_TOPIC` / file), the observable (sliding `W`, snapshots `t_k = k·S`,
  oracle = correct answer), per-snapshot output record `(snapshot_ts, key, value)` with
  settled last-wins semantics, per-query key/value binding, and a per-engine conformance
  checklist. Everything below targets this.
- ✅ 1.6  **Accuracy checker** (process-level, engine-agnostic): scoring core (`accuracy.py`,
  now null-safe for membership queries) + driver (`checker.py`) that reads an engine's
  `RESULTS_TOPIC`/output file (JSONL/CSV), materializes settled last-wins
  `snapshot_ts -> {key: value}`, aligns to the oracle grid, and scores it — Flink,
  RisingWave and Rust are all graded by this one path. `python3 checker.py --selftest`
  round-trips every reference maintainer green; adversarially verified it detects value
  perturbations and off-grid (orphan) records.
- ✅ 1.7  **Benchmark harness** (`benchmark.py`, engine-agnostic): throughput (events/sec,
  the `Primary metric:` line the Perf Evaluator reads) + per-snapshot latency p50/p95/p99.
  `PythonMaintainerRunner` (real numbers now) + `SubprocessRunner` (Phase-2 Rust binary).
  Live-engine end-to-end marker latency rides the Kafka path (CONTRACT.md §7), reserved.
- ✅ 1.8  **Kafka substrate + load driver**: `docker-compose.yml` (single KRaft broker +
  RisingWave + Flink services; `docker compose config` valid) and `load_driver.py` — replay
  `events.csv` into `EVENTS_TOPIC` at `--asap`/`--rate`/`--speed`, stamping `ingest_ts` for
  latency; `--to file` fallback works offline (kafka-python optional). `dump_results.py`
  turns a live engine's `RESULTS_TOPIC` (incl. upsert tombstones) into a checker input file.
- ✅ 1.9  **Flink baseline** (`baselines/flink/`): Flink SQL for all four queries (HOP TVF +
  HAVING / COUNT DISTINCT / window Top-N / interval anti-join), Kafka in/out per the I/O
  contract, README with the half-open-window accuracy caveat. Ready to run once Docker is up.
- ✅ 1.10 **RisingWave baseline** (`baselines/risingwave/`): shared Kafka `SOURCE` + one
  `MATERIALIZED VIEW` + upsert `SINK` per query, README. Same protocol, graded by the same
  checker.
- ✅ 1.11 **Query set expansion**: Q2 active-users COUNT(DISTINCT), Q3 top-k cost, Q4
  anti-join stalled-requests — each = one `.sql` (`queries/`) + oracle (generic, `oracle.py`
  over `queries.py` registry) + exact reference maintainer (`maintainers.py`). No new data.
  All four self-test exact (301/301); suite shows 838 retractions. (Q2's distinct count
  saturates under W=60s → its role is the distinct STATE-COST / approx-distinct axis, not
  retraction; Q1/Q3/Q4 carry the retraction load — reported honestly by `run.py`.)
- ✅ 1.12 **Results collection** (`results.py`): `{engine × query × metric}` → `results.md`
  + `results.json`. Reference maintainer (bespoke stand-in) graded + benchmarked across all
  queries now; `--add ENGINE QUERY FILE` merges live Flink/RisingWave dumps when infra is up.

**PHASE 1 COMPLETE (agent-free).** Runnable now: `run.py` (harness self-test),
`checker.py --selftest`, `benchmark.py`, `results.py`. Needs Docker (not run here):
the Flink/RisingWave live baselines — SQL + adapters + compose are written and validated,
awaiting `docker compose up`.

---

## PHASE 2 — Extend VibeServe for streaming + Rust (CPU/native)
**Goal:** VibeServe can **build, run, accuracy-check, and profile a Rust streaming
engine** — i.e. the Implementer agent can synthesize `engine.rs`, and the loop grades it.
**Done when:** a *hand-written* trivial-but-correct Rust engine passes the accuracy
checker and emits a perf metric *through VibeServe's loop machinery* (agent not involved
yet). This proves the plumbing before spending agent budget.

- ☐ 2.1  **New `native`/CPU backend** in `ComputeBackend`: local/docker sandbox (no GPU,
  no CUDA container, no device passthrough), build = `cargo build --release`, run =
  native process, "profiler" = the Phase-1 benchmark harness. Wire `backends/`,
  `sandbox/`, `run_environment.py`. (Lighter than the GPU backends.)
- ☐ 2.2  **New domain template** (`templates/_domain/streaming-ivm.md`): non-monotonic +
  windowed IVM background knowledge + the Judge's accuracy gates (must match oracle
  within tolerance across snapshots).
- ☐ 2.3  **New modality template** (`templates/_modality/stream-snapshot/`): the I/O
  contract from 1.5 as the per-task I/O spec (event-stream-in / snapshot-out).
- ☐ 2.4  **Rust engine scaffold**: a Cargo project skeleton the agent fills — stream
  reader (Kafka/stdin per contract), snapshot emitter, a `Maintainer` trait/entrypoint.
- ☐ 2.5  **Accuracy checker adaptation**: grade a *compiled* engine at the process level
  vs oracle (VibeServe's stock checker imports a Python `main.py`; a Rust binary is
  graded as a subprocess). Reuse 1.6.
- ☐ 2.6  **Benchmark adaptation**: run the Rust binary under the 1.7 harness; emit
  `Primary metric:`.
- ☐ 2.7  **`OBJECTIVE.md`** for the streaming target (goal + workload + headline metric +
  accuracy gate), mirroring the `examples/*/OBJECTIVE.md` style.
- ☐ 2.8  **Plumbing smoke test**: hand-write a trivial correct Rust engine; run the full
  inner loop (impl→judge→perf) on it manually. Validates 2.1–2.7 end-to-end.

---

## PHASE 3 — Run synthesis + gather results
**Goal:** let the agent synthesize bespoke engines and compare the best against the
baselines across the sweep.
**Done when:** a results table shows the best synthesized engine vs Flink/RisingWave
(+ Materialize opt.) on {throughput, latency, accuracy} across the sweep matrix, with
honest flagging of where the bespoke engine does and does not win.

- ☐ 3.1  Configure `agent.toml`/CLI: outer loop (`agent`/`plain`/`evolve`), provider,
  rounds/retries. Point `--ref`/`--acc-checker`/`--bench` at the streaming target.
- ☐ 3.2  Run the loop; collect candidate `engine.rs` per round (git checkpoints).
- ☐ 3.3  Grade candidates: accuracy (vs oracle) + throughput + latency.
- ☐ 3.4  Compare best synthesized engine vs Flink / RisingWave (/ Materialize) baselines.
- ☐ 3.5  **Sweep matrix** (`DESIGN.md` §8): offered rate × window size × key cardinality
  × retraction intensity × query. Report the full grid.
- ☐ 3.6  Analysis + plots + writeup; explicitly flag non-winning regions.

---

## Cross-phase invariants
- The DuckDB oracle is the single source of truth; accuracy is measured, symmetric,
  per-snapshot; the workload is engine-neutral; v1 is in-order (out-of-order/late data is
  a later deliberate accuracy-stress axis).
- Reuse over rebuild: 1.6 (accuracy checker) and 1.7 (benchmark) are written **once** to
  be engine-agnostic, then reused for Flink, RisingWave, the Rust reference, and the
  synthesized engine. Do not fork per engine.
