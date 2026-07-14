# I/O Contract v1

The fixed interface every engine under test — the DuckDB oracle, Flink, RisingWave, the
Rust reference, and the VibeServe-synthesized engine — conforms to. If an engine reads
this input and produces this output, it is comparable and gradable by `accuracy.py`.
Parameters (`W`, `S`, `budget`, topics) come from `config.py`; nothing here is hardcoded
per engine. The workload is engine-neutral (§4).

`contract_version: 1` · v1 assumes an **in-order** stream (out-of-order / late data is a
later, deliberate accuracy-stress axis, not part of v1).

---

## 1. Input: the event stream

**Logical record** (one LLM-serving request-completion event):

| field | type | notes |
|-------|------|-------|
| `ts` | double | **event time**, seconds since sim epoch `T0=0`. Canonical time used by all window math. Non-decreasing in v1. |
| `request_id` | string | unique event id (`req-<n>`); also the latency-marker handle |
| `user_id` | string | primary grouping key for Q1 |
| `project_id` | string | tenant grouping (Q2+) |
| `session_id` | string | |
| `model` | string | (Q2/Q3) |
| `input_tokens` | int | |
| `output_tokens` | int | |
| `reasoning_tokens` | int | |
| `cost_usd` | double | (Q3) |
| `latency_ms` | int | |
| `status` | string | `success` \| `error` \| `timeout` (Q4) |
| `finish_reason` | string | |

`total_tokens := input_tokens + output_tokens + reasoning_tokens` (derived, not stored).

**Serialization:** one JSON object per event (UTF-8).
**Transports (two, same records):**
- **File** (`events.csv` today; JSONL accepted): for the oracle and offline replay.
- **Kafka** topic `EVENTS_TOPIC` for live engines. Message value = the JSON event.
  Message key = `user_id`. Producer emits in non-decreasing `ts` order.

**Event-time / watermarks:** engines use `ts` as the event-time attribute (e.g. Flink
`TO_TIMESTAMP_LTZ(ts*1000, 3)`), with **bounded-out-of-orderness = 0** in v1.
- **Correctness runs:** single partition (removes cross-partition watermark skew as a
  variable — truth is unambiguous).
- **Throughput runs:** 8 partitions keyed by `user_id` (per-key order preserved, which is
  all per-user aggregation needs). Any settling skew this introduces is measured, not
  hidden.

---

## 2. The observable (definition of the correct answer)

- A query has a **sliding window** of length `W` (`WINDOW_SECONDS`). A row is in-window
  at evaluation time `t` iff `(t - W) < ts <= t`.
- The result is **sampled at snapshot times** `t_k = k · S` (`S = SNAPSHOT_INTERVAL`),
  `k = 0 … floor(SIM_DURATION / S)`, aligned to `T0`.
- The **correct answer at `t_k`** is the query evaluated with `now() = t_k`. The DuckDB
  oracle (`oracle.py`) is the reference producer of this answer.

Sampling continuous-sliding truth at interval `S` coincides exactly, at the `t_k`, with a
`HOP(size = W, slide = S)` windowed aggregation whose `window_end = t_k`. So engines may
realize the observable however they like (hop window, temporal filter, custom
maintainer). **Whatever an engine cannot reproduce at the `t_k` is its per-snapshot
accuracy loss** (§4) — we do not bend the observable to any engine.

---

## 3. Output: per-snapshot results

**Logical record** (one flagged row at one snapshot):

| field | type | notes |
|-------|------|-------|
| `snapshot_ts` | double | must equal some `t_k` (= `window_end`) |
| `key` | string | the grouping key (see per-query binding, §5) |
| `value` | double \| null | the aggregate measure; `null` for pure-membership queries |

**Serialization:** one JSON object per record.
**Transports:**
- **File** (JSONL/CSV): oracle and offline engines.
- **Kafka** topic `RESULTS_TOPIC.<query>`: live engines emit result records here.

**Settled semantics (v1):** an engine may emit multiple records for the same
`(snapshot_ts, key)` as its changelog updates (insert → retract → re-insert). The scorer
takes the **last record per `(snapshot_ts, key)`** (by output offset / order) after the
stream fully drains — i.e. the *settled* value. Absence of a `(snapshot_ts, key)` means
the key is not flagged at that snapshot.

**Upsert / tombstone retraction:** live engines with an upsert-kafka sink (RisingWave,
Flink upsert) signal a retraction with a Kafka **tombstone** (null message *payload*, keyed
by `(snapshot_ts, key)`). `dump_results.py` drops tombstones → the key becomes absent at
that snapshot, matching settled semantics. This is distinct from the `value` *field* being
null (the Q4 membership rows), which is preserved. `checker.py` verified to collapse stale
changelog updates to the settled value and to flag off-grid `snapshot_ts` as orphans.

Only flagged rows are emitted (rows passing the query's `HAVING` / membership condition).
An empty snapshot = no records with that `snapshot_ts`.

---

## 4. Correctness = measured, symmetric metric (not a gate)

The scorer materializes each side into `snapshot_ts -> {key: value}` and compares per
snapshot via `accuracy.py`: exact-match snapshot rate (headline), set precision/recall/F1
on `key` membership, and value MAE / max-error on `value`. Every engine — Flink,
RisingWave, bespoke — is scored identically. Fast-but-approximate shows as high
throughput *and* lower accuracy; nothing is disqualified. See `DESIGN.md` §5-6.3.

**Scorer input form (already produced by the oracle/run harness):**
`[(snapshot_ts, {key: value}), …]` for both oracle and engine, aligned on `snapshot_ts`.

---

## 5. Per-query key/value binding

| query | `key` | `value` | membership condition |
|-------|-------|---------|----------------------|
| **Q1 `metering`** (built) | `user_id` | `SUM(total_tokens)` over window | `> budget` |
| **Q2 `active_users`** (built) | `project_id` | `COUNT(DISTINCT user_id)` over window | all in-window projects |
| **Q3 `top_cost`** (built) | `model` | `SUM(cost)` over window, in integer **micro-dollars** | rank ≤ `k` |
| **Q4 `stalled`** (built) | `request_id` | `null` | `status≠success` AND no later same-user success in window |

All four are built: `queries/<name>.sql` (oracle definition), a `queries.py` registry entry,
and an exact reference maintainer (`maintainer.py` Q1, `maintainers.py` Q2-Q4). Each query =
its own `RESULTS_TOPIC.<query>`. Adding a query needs **no new event data**.

Q3 `value` is exact integer micro-dollars (`PRICE_MILLI[model] * total_tokens`), so oracle
and maintainer agree bit-for-bit and the ranking never flips on float drift.

---

## 6. Conformance checklist (for each new engine adapter)

1. Consume `EVENTS_TOPIC` (or the file), using `ts` as event time, bounded-out-of-order 0.
2. Compute the query per §2/§5 at snapshots `t_k = k·S` aligned to `T0`.
3. Emit result records (§3) to `RESULTS_TOPIC.<query>` (or a file) — flagged rows only,
   `snapshot_ts = t_k`.
4. Declare the engine's window realization + any approximation (e.g. slide, approx
   distinct) — this is reported alongside accuracy (`DESIGN.md` §9).
5. Grade with `accuracy.score(oracle_results, engine_results)`.

## 7. Reserved / out of scope for v1

- **Latency probing** (marker events + output arrival wall-clock) — spec in TASKS.md 1.7;
  markers ride the same `EVENTS_TOPIC` using distinctive `request_id`/`user_id`, and do
  not change this contract.
- **Out-of-order / late data**, **exactly-once vs at-least-once output**, and **multiple
  concurrent queries per engine** — later versions; bump `contract_version`.
