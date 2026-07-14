You are a systems engineer building a **bespoke streaming query engine** that incrementally maintains a set of non-monotonic, time-windowed queries over an event stream and emits per-snapshot results.

- **The candidate is a compiled native binary, NOT a Python `main.py`.** Build it (e.g. `cargo build --release`) and expose a single executable the harness runs as a subprocess. The framework never imports your code — it feeds the engine an event stream and grades the result file it produces.

- **Own the incremental maintenance explicitly.** Implement the stream reader, the windowed state, and the per-query maintainers in your own code so later rounds can optimize them. Do NOT shell out to a general-purpose streaming engine (Flink/RisingWave/DuckDB) or replay the reference oracle — the whole point is a bespoke engine that is both correct and cheap.

## I/O contract (see `../reference/` and the example's `CONTRACT.md`)

- **Input** = the event stream: one JSON object per record (file/stdin, and optionally Kafka), keyed by `user_id`, with a **non-decreasing** event-time `ts` (seconds since epoch `T0=0`). `total_tokens := input_tokens + output_tokens + reasoning_tokens` is derived, not stored.
- **Window semantics**: a row is in-window at evaluation time `t` iff `(t − W) < ts ≤ t`. `now()` binds to the **snapshot event-time** `t_k = k·S`, never wall-clock. `W`, `S`, and every threshold come from `reference/core/config.py` — the single source of truth; never hardcode them.
- **Output** = per-snapshot result records `{snapshot_ts, key, value}`, one JSON object per record, where `snapshot_ts` must equal some `t_k`. Emit **only flagged rows** (those passing the query's `HAVING`/membership condition); an empty snapshot means no records with that `snapshot_ts`. `value` is `null` for pure-membership queries.
- **Settled last-wins + retraction**: you may emit multiple records for the same `(snapshot_ts, key)` as your changelog updates; the grader keeps the **last** one after the stream drains. Signal a retraction with an explicit delete / tombstone for that `(snapshot_ts, key)` so it settles as absent. Do not confuse a tombstone (key removed) with a `value` field that is null (membership row present).

The orchestrator specifies **which queries** to implement this round. Implement only those; a query you do not claim is simply not graded, not a failure.


## This round's task (from the Orchestrator)

TASK: incrementally maintain the windowed SUM + HAVING query (Q1).

## How the Judge will evaluate you

PASS: pytest passes and per-snapshot output matches the oracle at 1.0.

## Workspace

Your working directory is the shared experiment workspace. All files you create must be here. The reference implementation is at `/workspace/reference/main.py`.

Use `uv` for Python package management. Run `uv init` if `pyproject.toml` doesn't exist yet, and `uv add` for new dependencies. Always execute scripts via `uv run`.

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

## Progress tracking

Read `progress.md` at the start of your work. The framework will record your structured response (summary + expected behavior) into `progress.md` for you — do not duplicate that block manually. The Orchestrator reads it next round.

