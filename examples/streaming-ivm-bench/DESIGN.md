# Benchmark Design: Bespoke vs. General Streaming Engines on Non-Monotonic + Windowed Workloads

**Status:** design spec for review (no measurement code written yet). MVP correctness
harness already built and green — see `README.md`.

## 1. Claim under test

> A **bespoke, workload-specialized database engine** (synthesized by vibe-database)
> maintains **non-monotonic + time-windowed** SQL results **more efficiently** than
> **general-purpose streaming engines** — specifically Apache Flink and Materialize.

**Early-stage metrics:** **throughput**, **latency**, and **per-snapshot accuracy**
(§6). Cost-per-query (resource-$ at a latency SLO) is **deferred** to a later stage —
spec retained in §6.4 but not measured in v1.

Novelty is **synthesis / specialization**, not incremental non-monotonic maintenance
itself (DBSP/Feldera/Materialize already do that). The benchmark exists to quantify
what specialization buys against the best general runtimes.

**Two governing principles (from review):**
1. **Correctness is a measured, symmetric metric — not a gate.** The oracle defines
   truth; *every* engine (Flink, Materialize, and the bespoke engine) is scored on
   per-snapshot deviation. Fast-but-approximate is a visible tradeoff, not a
   disqualification.
2. **Workload-first, engine-neutral.** We generate a traditional non-monotonic +
   time-window workload with one well-defined correct answer, tuned to *no* engine.
   Whatever an engine cannot express exactly surfaces as its own accuracy loss.

## 2. Systems under test (SUTs)

| system | role | notes |
|--------|------|-------|
| **Bespoke engine** | protagonist | Hand-written **Rust** reference first (sets the achievable bar), then the **vibe-database-synthesized** engine (the research artifact). *Not* the Python maintainer — that is a correctness reference only, not a throughput contender. |
| **Apache Flink** | baseline | Flink SQL job over Kafka, event-time + watermarks. |
| **Materialize** | baseline | Materialized view / temporal-filter query over Kafka. |
| **DuckDB oracle** | **ground-truth definer, NOT a SUT** | Batch recompute per snapshot = the one correct answer. Every SUT is *scored* on per-snapshot deviation from it (accuracy is a metric, not a pass/fail gate). |

Optional later: RisingWave, Feldera (both IVM-native — a tougher, informative bar).

## 3. Workload & data

**Domain:** LLM-serving observability / metering telemetry (topical; schema validated
against Langfuse / OpenInference / OTel GenAI — see
`../proposals/llm-observability-framing-evidence.md`).

**Event schema** (already emitted by `generate.py`): `ts, request_id, user_id,
project_id, session_id, model, input_tokens, output_tokens, reasoning_tokens,
cost_usd, latency_ms, status, finish_reason`.

**Generator:** deterministic + seeded (byte-identical across runs), baseline Poisson
traffic + whale bursts that force threshold crossings in **both** directions.

**Engine-neutral by construction.** The generator and the snapshot cadence are defined
by the *workload*, never tuned to any engine's window model (no aligning to Flink's hop
slide, no favoring Materialize's temporal filters). There is exactly one correct answer
— the oracle's — and each engine is graded against it.

**Design crux (the knob that decides whether the claim is interesting):** the workload
must make retraction and window maintenance *matter*. High event rate + meaningful
window + high refresh/freshness demand + real key churn. If the window is tiny or
refreshes are rare, a general engine is already fine and the gap vanishes. The sweep
(§8) deliberately walks this frontier and we **report where the gap opens and where it
closes** — no silent favorable-point cherry-picking.

## 4. Query set

Start with **one** query proven across all engines, then widen. Each query pairs a
non-monotonic operator with a temporal window and must retract.

| # | name | operators | status |
|---|------|-----------|--------|
| Q1 | token metering / quota | windowed `SUM` + `HAVING` threshold | **built** (oracle + Python ref) |
| Q2 | active-user cardinality | windowed `COUNT(DISTINCT user)` | planned |
| Q3 | top-k costliest models/users | windowed `SUM` + `ORDER BY ... LIMIT k` | planned |
| Q4 | stalled requests | anti-join / `NOT EXISTS` (no completion in window) | planned |

Q2–Q4 need **no new data** — the generator schema already carries `model`, `status`,
`session_id`, `cost_usd`.

**Per-dialect equivalence is a first-class requirement.** The same query is written in
DuckDB SQL (oracle), Flink SQL, Materialize SQL, and the bespoke spec. They are only
comparable if all match the oracle changelog (§5). Semantic reconciliation of sliding
vs. hopping windows is a known threat — see §9.

## 5. Correctness methodology (ground truth + how engines are scored)

- Define snapshot points at `SNAPSHOT_INTERVAL` in **event-time**, workload-defined
  (not tuned to any engine).
- Oracle: at snapshot `t`, batch-recompute the query over events with `ts <= t`, with
  `now()` **bound to `t`** (never wall-clock). The sequence of result sets is the one
  correct reference changelog.
- **Sampling each SUT (settled per-snapshot):** for each snapshot `t`, take the engine's
  *settled* result for the window ending at `t` — i.e. after it has fully processed all
  events with `ts <= t` (watermark past `t` / view quiesced). This isolates *what answer
  the engine computed* from *how fast* (latency is measured separately in §6.2). Live /
  convergence-lag accuracy is a later refinement (§6.3).
- **Accuracy is a measured, symmetric metric — not a gate.** Every SUT (Flink,
  Materialize, and the bespoke engine alike) is scored on its per-snapshot deviation
  from the oracle (§6.3). An engine that is fast but approximate simply reports high
  throughput *and* lower accuracy — the tradeoff is shown, not hidden, and nothing is
  disqualified.
- Integer token sums ⇒ exact equality is well-defined (no float tolerance) for the
  exact-match measure; value-error measures use the numeric deviation.

## 6. Metric definitions

Measured at **steady state** only (warmup discarded: JVM JIT, buffer fill, view
hydration). ≥3 repetitions; report median + spread. All SUTs consume the identical
seeded event log via the identical Kafka substrate. **Throughput and latency are
measured independently of accuracy** — an engine is never excluded for being wrong; its
accuracy is simply reported alongside.

### 6.1 Throughput
Max sustained **input events/sec** with **bounded output lag** over a sustained window
(offered load ramped until output/consumer lag grows without bound). Each engine gets
the **same CPU/RAM budget** for this test (apples-to-apples saturation).

### 6.2 Latency
Event→result-visible latency at a **fixed sub-saturation offered rate** (same rate for
all engines, e.g. 50% of the slowest engine's saturation). Reported **p50 / p95 / p99**.
Measured with **application-level markers**: inject distinctive probe events whose
effect on the output is unambiguous (e.g. a synthetic key crossing the threshold), and
time ingest→appearance-in-output-sink. Repeated throughout the run.

### 6.3 Per-snapshot accuracy  (first-class metric, symmetric across all engines)
Compare each engine's settled per-snapshot result `R_e(t)` (§5) to the oracle `R_o(t)`.
Reported as a suite so partial correctness is visible:

- **Exact-match snapshot rate** (headline): fraction of snapshots where `R_e(t) == R_o(t)`.
- **Set precision / recall / F1** on flagged-key membership per snapshot, averaged over
  snapshots — credits "missed one key" over "missed all."
- **Value error** on the maintained aggregate (e.g. windowed_tokens): mean / max
  absolute error over keys and snapshots — catches right-set-but-wrong-number (e.g. an
  engine's window granularity being coarser than the workload's).
- **(Later) convergence lag:** once we measure *live* (not settled) output, how many
  snapshots an engine stays wrong after each change — this ties accuracy to latency and
  exposes eventual-consistency behavior. Deferred with cost.

Every engine, bespoke included, is graded identically. This is the metric that captures
"Flink/Materialize got a snapshot wrong ⇒ accuracy loss," and equally holds the bespoke
engine to account.

### 6.4 Cost per query  — DEFERRED (spec retained for a later stage)
*Not measured in v1.* When enabled: fix a latency SLO (default p99 ≤ 500 ms); run a
fixed sub-saturation rate `R0` for duration `T` holding the SLO; sample **only the
engine's processes** via cgroups/`docker stats` for **CPU-seconds** and **peak/mean
RSS** (Kafka, load driver, probe excluded); price via a fixed on-demand sheet
(`$ = vCPU_h × price_vCPU_h + GB_h × price_GB_h`); normalize
`cost_per_billion_events = $ / (R0 × T) × 1e9`. Streaming analog of ClickBench's cost
column; where a specialized single-process engine should beat JVM Flink.

## 7. Fairness rules (methodology — review these carefully)

- **One ingestion substrate:** Kafka (both baselines have first-class connectors). Same
  topic, partitions, and offered-rate schedule for every SUT.
- **Same resource budget** for throughput/latency runs (equal cores + RAM cap via
  container limits). Cost runs instead hold rate fixed and *measure* resources.
- **Warmup discarded**, steady-state window only; ≥3 reps; median + spread.
- **Identical data** (one seed) and **identical snapshots** across SUTs — both
  workload-defined and **tuned to no engine** (no aligning snapshots to Flink's hop
  slide, etc.).
- **Truth is engine-neutral and fixed** (the oracle). Each engine is graded on deviation
  (§6.3); no engine's semantics are privileged as "correct."
- **Single-node** to start (removes network-shuffle variance); distributed noted as
  future work and its absence stated as a scope limit, not hidden.

## 8. Parameter sweep matrix

Walk the frontier where specialization should matter:

- **Offered rate:** {10k, 50k, 100k, 500k} events/s (or engine max).
- **Window size W:** {5 s, 60 s, 600 s} (bigger W ⇒ more in-window state ⇒ more work to
  maintain; tests where efficient incremental maintenance pays off).
- **Key cardinality:** {1e3, 1e5, 1e7} distinct users (state scaling).
- **Retraction intensity:** low/med/high churn (fraction of events that trigger a
  threshold crossing) — the non-monotonic stress axis.
- **Query:** Q1–Q4.

Report the full grid; **explicitly flag regions where the bespoke engine does NOT win**
(honest boundary of the claim).

## 9. Threats to validity (state these in any writeup)

- **Dialect semantic mismatch → now a *measured accuracy dimension*, not a threat to
  reconcile.** Flink windows are hop/tumble/cumulate; Materialize temporal filters are
  closer to continuous sliding; the oracle defines the neutral truth. We do **not** bend
  the workload to fit any engine; whatever an engine cannot express exactly shows up as
  its per-snapshot accuracy loss (§6.3). Caveat to state plainly: an engine may *choose*
  a more accurate but costlier configuration (e.g. tiny hop slide) — so accuracy numbers
  must always be read **together with** throughput/latency, and we report the config used.
- **JVM warmup** vs native start: discard warmup; report steady-state; note cold-start
  separately (it matters for the "bespoke is cheap to spin up" story).
- **Kafka as the bottleneck:** verify the substrate can out-supply the fastest engine
  (over-provision partitions/brokers) so we measure engines, not Kafka.
- **Python maintainer is not a performance SUT** — used only as a correctness reference
  and to validate the measurement harness before JVM engines exist.
- **Bespoke-engine implementation quality** confounds "specialization": the hand-written
  Rust reference isolates *what specialization achieves*; the synthesized engine then
  shows *what vibe-database achieves* against that bar.
- **Late/out-of-order events & watermarks:** MVP assumes in-order event-time. A late-data
  policy must be fixed identically across engines before claiming parity.

## 10. Phased implementation plan

- **Phase 0 ✅** correctness harness: generator + DuckDB oracle + Q1 + Python maintainer + snapshot diff. *Green (301/301).*
- **Phase 1** measurement plane: Kafka (docker-compose) + rate-controlled load driver + marker-based latency probe + cgroup resource sampler. Validate against the Python maintainer (no JVM yet).
- **Phase 2** Flink SUT: Flink SQL on Kafka; certify vs oracle; throughput/latency/cost.
- **Phase 3** Materialize SUT: same protocol.
- **Phase 4** Bespoke SUT: hand-written Rust reference, then vibe-database-synthesized.
- **Phase 5** sweep matrix (§8) → tables + plots.

## 11. Open decisions to resolve before Phase 1 code

*(Cost/SLO/price-sheet deferred with §6.4 — not gating v1.)*

1. **Throughput budget**: fixed cores/RAM per engine — what values?
2. **Kafka topology**: partitions, single vs multi broker.
3. **Bespoke engine language** for the reference: confirm **Rust** (vs C++).
4. **Late-data policy**: keep MVP's in-order assumption for v1, or model lateness now?
   (Directly affects accuracy numbers — an in-order stream removes lateness as a
   confound; introducing it later is a deliberate accuracy-stress axis.)
5. **Settled-accuracy sampling**: confirm we grade engines on settled per-window results
   in v1 (live/convergence-lag deferred with cost).
