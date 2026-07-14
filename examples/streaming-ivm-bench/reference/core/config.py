"""Shared benchmark parameters.

Single source of truth for the generator, the DuckDB oracle, and the incremental
maintainer so all three agree on window/threshold/snapshot semantics.

The MVP workload is TOKEN METERING / QUOTA:

    per user, SUM(total_tokens) over the last WINDOW_SECONDS (sliding, event-time),
    flag users whose windowed sum exceeds BUDGET_TOKENS.

This is non-monotonic: a user ENTERS the flagged set on a burst and LEAVES it
(retraction) when the burst ages out of the window. That leave-edge is the property
under test -- the point where general streaming engines can lose accuracy (or pay to
preserve it), and where a bespoke engine aims to be both correct and cheap. The
workload is engine-neutral: it is defined by the data, not tuned to any engine.
"""

import os

# --- workload semantics (generator, oracle, maintainer must all use these) ---
WINDOW_SECONDS = 60.0  # sliding window length W; row in-window at t iff (t - W) < ts <= t
BUDGET_TOKENS = 60_000  # HAVING threshold: flag users with windowed tokens > BUDGET
SNAPSHOT_INTERVAL = 1.0  # emit/compare the maintained result every this many sim-seconds
SIM_DURATION = 300.0  # total event-time span simulated

# --- query knobs ---
TOP_K = 3  # Q3: report the top-K costliest models per window

# Exact integer price in MILLI-dollars per 1k tokens (mirrors the generator's cost_usd:
# cost_usd == PRICE_MILLI[model] * total_tokens / 1e6, all integer -> no float drift, so
# oracle and maintainer agree exactly on the top-k cost aggregate). Keep in sync with the
# `price` map in generate.py.
PRICE_MILLI = {
    "gpt-4o": 5000,
    "claude-sonnet-5": 3000,
    "claude-opus-4-8": 15000,
    "gpt-4o-mini": 600,
    "llama-3-70b": 900,
}

# --- transport / infra (Phase 1.8-1.10; see docker-compose.yml, baselines/) ---
KAFKA_BOOTSTRAP = (
    "localhost:29092"  # HOST listener (load driver + dumper); containers use kafka:9092
)
EVENTS_TOPIC = "llm-events"  # input event stream (contract §1)
RESULTS_TOPIC_PREFIX = "results"  # output topic per query = f"{prefix}.{query}" (contract §3)
EVENTS_PARTITIONS_CORRECTNESS = 1  # single partition => unambiguous truth (contract §1)
EVENTS_PARTITIONS_THROUGHPUT = 8  # keyed by user_id for scale runs (contract §1)

# --- generator knobs ---
SEED = 42
N_USERS = 50
N_PROJECTS = 6
MODELS = ["gpt-4o", "claude-sonnet-5", "claude-opus-4-8", "gpt-4o-mini", "llama-3-70b"]
BASE_RATE = 18.0  # baseline events/sec across ALL users (Poisson)
N_WHALES = 5  # users who periodically burst above budget then go quiet
WHALE_BURSTS = 3  # burst intervals per whale
WHALE_BURST_EVENTS = 55  # events packed into each burst
WHALE_BURST_SECONDS = 8.0  # burst duration (short => sum spikes, then ages out => retraction)

# --- files ---
# Anchored to this module's directory (reference/core/) so the oracle, generator, checker,
# and benchmark all read the SAME events file regardless of the process's working directory.
# (Was a bare "events.csv" in the flat layout; that broke once the code split into
# reference/ + accuracy_checker/ + benchmark/ run from different cwds.)
EVENTS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.csv")
