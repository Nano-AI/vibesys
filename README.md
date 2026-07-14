# vibe-database: Can AI Agents Build Bespoke Streaming Query Engines?

**An agentic loop that synthesizes bespoke streaming / database query engines — one per
`(query, workload, hardware)` target — instead of forcing every stream through a single
general-purpose engine.**

## Introduction

vibe-database explores a new approach to stream processing and incremental view maintenance:
instead of relying on one general-purpose engine (Flink, Materialize, RisingWave) to support
every query, workload, and hardware target, we use AI agents to generate a bespoke query engine
for each deployment scenario. The project asks whether long-horizon coding agents can synthesize
a complete streaming query engine end-to-end — the incremental-maintenance logic, retraction
handling, window state, correctness checks, and performance optimizations tailored to a specific
query.

The system is organized as a multi-agent optimization loop. An outer loop plans the search over
engine designs using persistent state (issues, memory, git history), while an inner loop
implements candidate engines, validates correctness against a batch-recompute oracle, and
measures throughput on the target benchmark. The flagship target is `streaming-ivm-bench`:
**non-monotonic, time-windowed** SQL over an append-only event stream, graded per-snapshot
against a DuckDB oracle — the regime where general streaming engines either lose accuracy or pay
heavily in state to stay correct.

## Architecture

The framework factors the work along two axes:

- **Outer loop** — a search policy operating over a git-recorded history of validated
  checkpoints. It picks the next optimization, dispatches one concrete task to the inner loop,
  and updates persistent planning state (issues, long-term memory file, commit graph).
- **Inner loop** — three role-specialized coding-agent invocations on a shared workspace:
  - *Implementer* writes/edits the candidate query engine.
  - *Accuracy Judge* runs the user-supplied checker against the oracle and inspects diffs /
    runtime behavior for reward-hacking patterns; only correct candidates exit the inner loop.
  - *Performance Evaluator* profiles the implementation and feeds bottleneck hints back to the
    policy.
- **Execution environment** — an isolated workspace that mounts the user-provided artifacts
  read-only (so the Implementer cannot edit the checker or reference) and exposes the target
  hardware plus profilers.

Each candidate is a git commit; the outer loop only advances on Judge-validated implementations,
so incorrect candidates can never derail subsequent rounds.

## Installation

Requires Python 3.11+.

```bash
uv sync
cp .env.example .env       # provider keys (Anthropic / OpenAI / Vertex / …)
cp agent.toml.example agent.toml
```

## Quickstart

```bash
# Agent outer loop, local CPU execution, streaming-ivm domain
vibe-database \
  --ref examples/streaming-ivm-bench/reference \
  --acc-checker examples/streaming-ivm-bench/accuracy_checker \
  --bench examples/streaming-ivm-bench/benchmark \
  --exp-name my-experiment \
  --backend cpu \
  --domain streaming-ivm \
  --modality stream-snapshot \
  --max-rounds 4
```

`--outer-loop` defaults to `agent`. Pass `--outer-loop plain` or `--outer-loop evolve` to
switch. See `vibe-database --outer-loop <kind> --help` for loop-specific flags.

A separate entry point exposes the issue MCP server used by the plain loop:

```bash
vibe-database-issue-mcp                       # serves issues.json over MCP
```

## Domains — pointing vibe-database at your problem space

A **domain** is the bundle of cross-cutting context the agents need for whatever you're
building: the background knowledge the implementer must read, and the correctness / performance /
integrity gates the judge enforces. It answers *"what kind of system is this, and what does
'good' mean here?"* — kept separate from the neutral prompt skeleton and from the per-task I/O
contract (`--modality`).

Select one with `--domain` (agent loop):

```bash
vibe-database --outer-loop agent --domain streaming-ivm ...   # default
vibe-database --outer-loop agent --domain generic ...         # no domain context
vibe-database --outer-loop agent --domain ./my-domain.md ...  # your own (a path)
```

`--domain` takes a **built-in name** (`streaming-ivm`, `generic`) **or a path** to your own
`.md` file anywhere on disk. A domain is just a single Markdown file: free-form description
prose, then `## implementer`, `## judge`, and (optionally) `## single_agent` sections that drop
into the prompts at one labelled point each. Omit `## single_agent` and it's derived from the
other two. Author your own by copying `generic.md` — no code change required.

Full authoring guide:
[`src/vibe_database/loops/agent/templates/_domain/README.md`](src/vibe_database/loops/agent/templates/_domain/README.md).

## Per-target inputs

Each evaluation target lives under `examples/<name>/`:

```
examples/<name>/
├── OBJECTIVE.md          # free-form deployment goal (query + workload + hardware + interface)
├── reference/            # exact-correctness reference maintainers + shared config
├── accuracy_checker/     # checker.py + oracle — the correctness gate
├── benchmark/            # benchmark.py + load levels — emits the metric to optimize
└── README.md             # human-readable description
```

`OBJECTIVE.md` is read at the start of every run and must live next to `--ref` (sibling, not
inside). See `examples/streaming-ivm-bench/` for the flagship scenario — its `OBJECTIVE.md`,
`CONTRACT.md`, and `DESIGN.md` document the queries, the snapshot grid, and the four flavors of
non-monotonicity the engine must handle.

For multi-objective evolutionary runs, drop an `objectives.toml` next to `OBJECTIVE.md` (or pass
`--objective name:max|min` flags) — see `vibe-database --outer-loop evolve --help`.

## Configuration (`agent.toml`)

```toml
[model]
name = "claude-sonnet-4-6"   # auto-detected provider for claude-* / gpt-* / gemini-*
# provider = "anthropic"     # optional override

[backend]
name = "cpu"                  # compiled query engines run on CPU

[agent]
backend = "cli"               # "cli" (codex/claude/gemini/opencode) or "deepagents"
cli_provider = "codex"        # which coding-agent harness to drive
# cli_model = "gpt-5-codex"   # override the model the CLI tool uses
# cli_timeout = 1800          # per-invocation timeout (seconds)

# Optional: benchmark load levels handed to the perf evaluator.
# [[perf_eval.load_levels]]
# rate = 1
# duration = 20
```

Provider credentials live in `.env` — see `.env.example`. The CLI flags `--agent-backend` /
`--cli-provider` / `--backend` override these.

The config is validated against a typed schema on load (`vibe_database/config.py`): unknown
sections or keys, unknown providers/backends, and missing required fields are rejected with an
error rather than silently ignored.

## Outputs

Every run creates `exp_env/<timestamp>-<name>/`:

```
exp_env/<run>/
├── workspace/                # the unified, git-tracked workspace (each round = one commit)
├── logs/
│   ├── run-*.log             # top-level run log
│   ├── run-*-roundNNN.log    # per-round agent log (agent loop)
│   ├── progress.md           # long-term memory file the Orchestrator reads/edits
│   ├── rounds.json           # per-round audit
│   ├── state.json            # cursor (plain loop)
│   ├── issues.json           # IssueBoard (plain loop)
│   └── population.json       # Individual list (evolve loop)
└── reference/                # snapshot of --ref at start
```

Resume any run with `--resume` (defaults to "latest"):

```bash
vibe-database --resume                  # newest run
vibe-database --resume 20260507-...     # specific dir
```

## Repository layout

```
src/vibe_database/
├── cli.py                        # single entry point: `vibe-database`
├── context.py                    # _RunContext: lifecycle + ctx.invoke()
├── agent_runner.py               # invoke wrappers + structured-response extraction
├── prompts.py                    # Jinja + backend-fragment renderer
├── schemas.py                    # Pydantic response schemas
├── config.py / constants.py
│
├── loops/                        # the three outer-loop search policies
│   ├── agent/                    # issue-tracker (Orchestrator-driven)
│   ├── plain/                    # Ralph-style queue-drain
│   ├── evolve/                   # population-based
│   └── profiler.py               # shared Performance Evaluator helper
│
├── sandbox/                      # execution-environment policy
│   ├── docker_sandbox.py         # (dormant — local exec is the default path)
│   ├── modal_sandbox.py          # (dormant)
│   └── run_environment.py
│
├── agents/                       # coding-agent harness abstraction
│   └── callbacks.py              # LangChain logger (deepagents path)
└── backends/                     # cpu compute backend

examples/streaming-ivm-bench/     # flagship streaming query-engine target
```

- **agent**: pre-round → profiler → orchestrator plan → implementer/judge
  retry up to `--max-retries-per-round` (default 3). Always exhausts
  `--max-rounds`; supports `revert_to_round` mid-loop.
- **plain**: drain `IssueBoard` (one impl + one judge per issue, BLOCK
  after `--max-attempts-per-issue`) → `perf_eval` (may file new issues).
  Early-exits when queue is empty and `perf_eval` files nothing.
- **evolve**: per generation × child: select parent (Pareto frontier with
  `--frontier-bias`, scalar softmax otherwise) + inspirations →
  `git checkout` parent tree → mutator → judge → profiler → commit.
  No early stop; runs the full `--max-generations × --children-per-generation`.

## Development

```bash
./scripts/format.sh                                # format checked Python dirs
./scripts/check_format.sh                          # check formatting for CI
uv run pytest                                       # full suite
uv run pytest tests/loops/plain/test_plain_loop.py  # one file
uv run pytest -k orchestrator                       # by keyword
```
