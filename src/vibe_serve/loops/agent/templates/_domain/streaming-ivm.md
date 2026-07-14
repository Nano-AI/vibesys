# Streaming incremental view maintenance (non-monotonic + windowed)

**Use for:** synthesizing a bespoke streaming query engine that incrementally maintains
**non-monotonic, time-windowed** SQL over an append-only event stream, graded per-snapshot
against a DuckDB batch-recompute oracle. This is the `streaming-ivm-bench` target — see its
`OBJECTIVE.md`, `CONTRACT.md`, and `DESIGN.md`.

The role sections below are injected into the base implementer/judge/orchestrator prompts.

## implementer

You are building a **bespoke incremental-view-maintenance (IVM) engine**. The hard part is
that the queries are **non-monotonic**: results must be **retracted**, not just added, as the
sliding window advances and as thresholds/rankings/membership flip. General streaming engines
either lose accuracy here or pay heavily in state to stay correct — your job is an engine that
is **both exact and cheap**.

- **Sliding window, snapshot-sampled.** Every query is maintained over a window of length `W`
  and reported on a grid `t_k = k·S`. A row is in-window at `t` iff `(t − W) < ts ≤ t`;
  `now()` binds to the snapshot event-time `t_k`, never wall-clock. As events age past
  `t − W` they leave the window and their contribution must be **removed** from the answer.
- **Four flavors of non-monotonicity** (implement the ones the orchestrator scoped this round):
  threshold retraction (windowed `SUM` + `HAVING` — a key drops below budget as a burst ages
  out), distinct-state (`COUNT(DISTINCT)` — decrement when the last occurrence expires),
  ranking churn (windowed Top-N — a model leaves the top-K as costs shift), and anti-join /
  `NOT EXISTS` membership (a request becomes/ceases-to-be "stalled" as a later success enters
  or expires from the window). Each needs its own retraction logic.
- **Emit a settled changelog.** Insert → retract → re-insert per `(snapshot_ts, key)` is fine;
  the grader keeps the last op after the stream drains. Retract with an explicit delete /
  tombstone. Read `W`, `S`, thresholds, `TOP_K`, and prices from `reference/core/config.py` —
  never hardcode them; they are shared with the oracle and generator.
- The Python maintainers in `reference/core/` are an **exact correctness reference and seed**,
  not a throughput contender. Study them for the semantics, then beat them on speed in your
  compiled engine.

## judge

Enforce **correctness parity with the oracle** as a hard gate before any performance credit:

- The engine's per-snapshot output, after settling (last-write-wins per `(snapshot_ts, key)`,
  honoring tombstone retraction), must reproduce `accuracy_checker/checker.py`'s oracle at
  **exact-match snapshot rate = 1.0** on the queries the candidate claims to support. A wrong
  snapshot is measured accuracy loss — advancing a candidate requires exactness on the
  implemented queries, not "close enough."
- **Retraction correctness is the crux.** Specifically check the non-monotonic transitions: a
  key that must *disappear* when its window contribution ages out or drops below threshold, a
  distinct count that must *decrement*, a Top-N member that must be *evicted*, an anti-join row
  that must *flip*. An engine that only ever inserts will look right early and drift wrong —
  reject it.
- **No reward hacking.** The engine must compute answers incrementally in its own code.
  Replaying/importing the DuckDB oracle, embedding a general-purpose streaming engine
  (Flink/RisingWave), or hardcoding expected snapshots is a fail even at 1.0 exact-match.
  Off-grid `snapshot_ts` (orphans) and hardcoded window params are red flags.

## orchestrator

Sequence rounds so **correctness leads and throughput follows**, one non-monotonic pattern at
a time:

- Start from the simplest exact case (windowed `SUM` + `HAVING` retraction, Q1 `metering`),
  get it to exact-match 1.0, then add the harder patterns — distinct-state (Q2), ranking churn
  (Q3), anti-join membership (Q4) — rather than attempting all four at once. Q4 (windowed
  anti-join) is the documented hard case where plain streaming SQL collapses; schedule it once
  the retraction machinery is solid.
- Never trade away exactness for speed: the metric is `events_per_sec` **at correctness
  parity**. Only pursue throughput optimizations (batching, tighter window state, better data
  layout) on queries already at 1.0. Keep the window/snapshot semantics sourced from
  `reference/core/config.py` across every round.
