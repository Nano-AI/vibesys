# differential-dataflow CPU superoptimization

The first runnable target for the `database` domain. It is an **in-place
superoptimization** of a real dataflow engine's own source: the vanilla
[differential-dataflow](https://github.com/TimelyDataflow/differential-dataflow)
crate, micro-optimized on its incremental `bfs` example. The agent edits the
vendored source in place; correctness is judged as **output-equivalence with a
pristine copy of the same round-0 engine, run live**, and the score is the CPU
the engine burns on a fixed workload. See `OBJECTIVE.md` for the full contract.

## Layout

- `engine/` — the vendored differential-dataflow source the agent **edits in
  place** (round 0 = vanilla; simultaneously the baseline and the starting
  point). The build tree (`target/`) is produced at run time and never vendored.
- `_ref_engine/` — a pristine, never-edited copy of the same round-0 source. The
  equivalence gate builds and runs it **live** to generate the golden output, so
  there is no stored answer to memorize.
- `reference/workload.py` — the single source of truth for the fixed `bfs`
  invocations (a canonical metric workload plus a perturbation workload), shared
  by the gate and the benchmark.
- `accuracy_checker/equivalence_gate.py` — rebuilds the candidate, runs it and
  the pristine engine on every workload, and requires **byte-identical**
  normalized output. Wired as `[accuracy]`.
- `benchmark/benchmark.py` — times the candidate's CPU-seconds on the metric
  workload and reports the headline
  `cpu_reduction_ratio = baseline_cpu_seconds / candidate_cpu_seconds` (round 0
  ≈ 1.0). Wired as `[benchmark]` with `metric = "cpu_reduction_ratio"`.
- `benchmark/baseline.json` — the round-0 CPU baseline, captured once on the
  reference box by `benchmark/capture_baseline.py`. It is **machine-specific**;
  recapture it if the host or the vendored source changes.

## Running the checks directly

The engine builds from source offline against a warm `~/.cargo` cache (Rust
toolchain required on `PATH`):

```bash
cd examples/database/differential-dataflow-cpu-bench
# build + correctness (rebuilds the candidate, then compares to the pristine engine)
uv run python accuracy_checker/equivalence_gate.py \
  --engine-cmd engine/target/release/examples/bfs \
  --rebuild-cmd "cargo build --release --example bfs -p differential-dataflow --offline --manifest-path engine/Cargo.toml"
# CPU metric
uv run python benchmark/benchmark.py \
  --engine-cmd engine/target/release/examples/bfs --output-json /tmp/perf.json
```

The behavioral-consistency gates (differential-fuzz, determinism,
crash/restart recovery, race-freedom) named in the domain prompts are added in a
follow-up alongside this target.
