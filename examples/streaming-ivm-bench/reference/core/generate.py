"""Deterministic LLM-serving event generator.

Emits a stream of request-completion events sorted by event-time to EVENTS_CSV.
The schema mirrors what real LLM-observability platforms (Langfuse / OpenInference /
OpenTelemetry GenAI) actually record, so the same log can drive richer queries later
(COUNT DISTINCT model, anti-join on status, top-k by cost, ...).

Seeded => byte-identical output across runs, which is what makes the oracle diff a
meaningful regression gate.

The traffic is baseline Poisson arrivals for all users PLUS a few "whale" users who
burst above budget and then fall quiet, guaranteeing threshold crossings in BOTH
directions (enter the flagged set, then retract out of it).
"""

import csv
import heapq
import random

import config


def _make_event(ts, rid, user_id, rng):
    input_tokens = rng.randint(80, 2000)
    output_tokens = rng.randint(40, 1200)
    # reasoning tokens only for some models/requests
    reasoning_tokens = rng.randint(0, 800) if rng.random() < 0.3 else 0
    total = input_tokens + output_tokens + reasoning_tokens
    model = rng.choice(config.MODELS)
    # crude cost model: $ per 1k total tokens, model-dependent
    price = {
        "gpt-4o": 5.0,
        "claude-sonnet-5": 3.0,
        "claude-opus-4-8": 15.0,
        "gpt-4o-mini": 0.6,
        "llama-3-70b": 0.9,
    }[model]
    cost_usd = round(price * total / 1000.0, 6)
    latency_ms = rng.randint(120, 9000)
    status = "success" if rng.random() < 0.95 else rng.choice(["error", "timeout"])
    finish = "stop" if status == "success" else ("length" if rng.random() < 0.5 else "error")
    return {
        "ts": round(ts, 4),
        "request_id": f"req-{rid}",
        "user_id": f"u{user_id:03d}",
        "project_id": f"p{user_id % config.N_PROJECTS}",
        "session_id": f"s{rid // 7}",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cost_usd": cost_usd,
        "latency_ms": latency_ms,
        "status": status,
        "finish_reason": finish,
    }


def generate():
    rng = random.Random(config.SEED)

    # A min-heap of (ts, user_id) arrival times. Baseline Poisson for all users +
    # dense whale bursts. We pop in ts order so the CSV is globally time-sorted.
    arrivals = []

    # baseline: exponential inter-arrival for the aggregate stream
    t = 0.0
    while t < config.SIM_DURATION:
        t += rng.expovariate(config.BASE_RATE)
        if t >= config.SIM_DURATION:
            break
        user_id = rng.randrange(config.N_USERS)
        heapq.heappush(arrivals, (t, user_id))

    # whales: first N_WHALES user ids, each bursts WHALE_BURSTS times
    for w in range(config.N_WHALES):
        for _ in range(config.WHALE_BURSTS):
            start = rng.uniform(0.0, config.SIM_DURATION - config.WHALE_BURST_SECONDS)
            for _ in range(config.WHALE_BURST_EVENTS):
                bt = start + rng.uniform(0.0, config.WHALE_BURST_SECONDS)
                heapq.heappush(arrivals, (bt, w))

    fields = [
        "ts",
        "request_id",
        "user_id",
        "project_id",
        "session_id",
        "model",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_usd",
        "latency_ms",
        "status",
        "finish_reason",
    ]

    rid = 0
    n = 0
    with open(config.EVENTS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        while arrivals:
            ts, user_id = heapq.heappop(arrivals)
            writer.writerow(_make_event(ts, rid, user_id, rng))
            rid += 1
            n += 1
    return n


if __name__ == "__main__":
    count = generate()
    print(
        f"wrote {count} events to {config.EVENTS_CSV} "
        f"(seed={config.SEED}, {config.SIM_DURATION:.0f}s sim)"
    )
