"""Reference INCREMENTAL maintainer for the metering query (with retraction).

This is a small, exact, hand-written maintainer used two ways: (1) as the harness
SELF-TEST -- an exact engine must score accuracy 1.0 against the oracle -- and (2) as a
correctness reference and design seed for the Rust engine the agent will synthesize. It
is NOT the performance contender (that is the synthesized Rust engine, benchmarked
against Flink/RisingWave); it is a Python correctness reference.

It maintains the windowed per-user token sum in one pass over the event stream, doing
O(1) amortized work per event -- an add on ingest and a subtract on expiry.

The subtract-on-expiry IS the retraction: when a user's windowed sum falls back below
budget because an old burst aged out, the user leaves the flagged set. That
non-monotonic leave-edge is the property under test -- where general streaming engines
can lose accuracy or pay to preserve it.

Semantics are bound to config so they match the oracle exactly:
    row in-window at snapshot t  iff  (t - W) < ts <= t
    => ingest when ts <= t ; expire (retract) when ts + W <= t
"""

import heapq

import config


class MeteringMaintainer:
    def __init__(self):
        self.sums = {}  # user_id -> current windowed token sum
        self.expiry = []  # min-heap of (expiry_ts, user_id, tokens)
        self.ingested = 0  # per-event work counters (informational)
        self.expired = 0

    def run(self, events, snapshots):
        """Stream `events` (list of dicts, ts-sorted) past increasing `snapshots`.

        Yields (now, result_dict) at each snapshot, where result_dict maps
        user_id -> windowed_tokens for users currently over budget.
        """
        W = config.WINDOW_SECONDS
        budget = config.BUDGET_TOKENS
        i = 0
        n = len(events)
        for now in snapshots:
            # 1. ingest arrivals with ts <= now  (the +delta)
            while i < n and events[i]["ts"] <= now:
                ev = events[i]
                tok = ev["input_tokens"] + ev["output_tokens"] + ev["reasoning_tokens"]
                uid = ev["user_id"]
                self.sums[uid] = self.sums.get(uid, 0) + tok
                heapq.heappush(self.expiry, (ev["ts"] + W, uid, tok))
                self.ingested += 1
                i += 1
            # 2. expire rows that have left the window: ts + W <= now  (the RETRACTION)
            while self.expiry and self.expiry[0][0] <= now:
                _, uid, tok = heapq.heappop(self.expiry)
                self.sums[uid] -= tok
                if self.sums[uid] == 0:
                    del self.sums[uid]
                self.expired += 1
            # 3. materialize the flagged set
            result = {uid: s for uid, s in self.sums.items() if s > budget}
            yield now, result
