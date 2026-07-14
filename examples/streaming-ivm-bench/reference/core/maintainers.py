"""Reference INCREMENTAL maintainers for Q2-Q4 (with retraction).

Companion to maintainer.py (Q1 metering). Each maintainer is a small, exact, hand-written
engine used two ways: (1) as a harness SELF-TEST -- an exact engine must score accuracy 1.0
against the DuckDB oracle -- and (2) as a correctness reference / design seed for the Rust
engine the agent will synthesize. None is the performance contender; each is a Python
correctness reference.

Every maintainer matches the oracle's window semantics exactly:
    row in-window at snapshot t  iff  (t - W) < ts <= t
    => ingest when ts <= t ; expire (retract) when ts + W <= t

Each `run(events, snapshots)` streams ts-sorted `events` past increasing `snapshots`,
yielding (now, result_dict) where result_dict maps key -> value for the flagged rows
(value is None for the pure-membership Q4).
"""

import heapq
from collections import deque

import config


class DistinctUsersMaintainer:
    """Q2: COUNT(DISTINCT user_id) per project over the window.

    Retraction is exact: a per-project multiset counts how many in-window events each user
    has; distinct = number of users with count > 0. When a user's last event expires the
    count drops to 0, the user is removed, and the distinct cardinality retracts.
    """

    def __init__(self):
        self.counts = {}  # project_id -> {user_id -> in-window event count}
        self.expiry = []  # min-heap of (expiry_ts, project_id, user_id)
        self.ingested = 0
        self.expired = 0

    def run(self, events, snapshots):
        W = config.WINDOW_SECONDS
        i, n = 0, len(events)
        for now in snapshots:
            while i < n and events[i]["ts"] <= now:
                ev = events[i]
                proj, uid = ev["project_id"], ev["user_id"]
                bucket = self.counts.setdefault(proj, {})
                bucket[uid] = bucket.get(uid, 0) + 1
                heapq.heappush(self.expiry, (ev["ts"] + W, proj, uid))
                self.ingested += 1
                i += 1
            while self.expiry and self.expiry[0][0] <= now:
                _, proj, uid = heapq.heappop(self.expiry)
                bucket = self.counts[proj]
                bucket[uid] -= 1
                if bucket[uid] == 0:
                    del bucket[uid]
                    if not bucket:
                        del self.counts[proj]
                self.expired += 1
            result = {proj: len(bucket) for proj, bucket in self.counts.items()}
            yield now, result


class TopKCostMaintainer:
    """Q3: top-K models by windowed cost (exact integer micro-dollars).

    Maintains a running per-model micro-dollar sum (add on ingest, subtract on expiry),
    then ranks. The ranking is non-monotonic: a model can leave the top-K purely because a
    costly request aged out. Integer arithmetic (PRICE_MILLI * total_tokens) means the sum
    and hence the ranking match the oracle bit-for-bit.
    """

    def __init__(self):
        self.sums = {}  # model -> windowed cost in micro-dollars
        self.expiry = []  # min-heap of (expiry_ts, model, micro)
        self.ingested = 0
        self.expired = 0

    def run(self, events, snapshots):
        W = config.WINDOW_SECONDS
        k = config.TOP_K
        price = config.PRICE_MILLI
        i, n = 0, len(events)
        for now in snapshots:
            while i < n and events[i]["ts"] <= now:
                ev = events[i]
                total = ev["input_tokens"] + ev["output_tokens"] + ev["reasoning_tokens"]
                micro = total * price[ev["model"]]
                self.sums[ev["model"]] = self.sums.get(ev["model"], 0) + micro
                heapq.heappush(self.expiry, (ev["ts"] + W, ev["model"], micro))
                self.ingested += 1
                i += 1
            while self.expiry and self.expiry[0][0] <= now:
                _, model, micro = heapq.heappop(self.expiry)
                self.sums[model] -= micro
                if self.sums[model] == 0:
                    del self.sums[model]
                self.expired += 1
            # rank by (cost desc, model asc) -- identical tie-break to the oracle's ORDER BY
            ranked = sorted(self.sums.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
            yield now, {model: micro for model, micro in ranked}


class StalledRequestsMaintainer:
    """Q4: failed requests with no later same-user success in the window (anti-join).

    Maintains the in-window event list per user (pruned by expiry). A failed request is
    stalled iff there is no later in-window success by the same user; equivalently, iff its
    ts is >= the latest in-window success ts for that user (or there is none). A later
    success RETRACTS the stalled request. This mirrors the oracle's NOT EXISTS exactly.

    It recomputes the stalled set per snapshot over in-window events (not O(1) per event) --
    acceptable for a correctness reference; the synthesized engine is free to be cleverer.
    """

    def __init__(self):
        self.by_user = {}  # user_id -> deque of (ts, status, request_id), in-window
        self.ingested = 0
        self.expired = 0

    def run(self, events, snapshots):
        W = config.WINDOW_SECONDS
        i, n = 0, len(events)
        for now in snapshots:
            lo = now - W
            while i < n and events[i]["ts"] <= now:
                ev = events[i]
                self.by_user.setdefault(ev["user_id"], deque()).append(
                    (ev["ts"], ev["status"], ev["request_id"])
                )
                self.ingested += 1
                i += 1
            # drop events that have aged out: in-window iff ts > now - W
            for uid, dq in list(self.by_user.items()):
                while dq and dq[0][0] <= lo:
                    dq.popleft()
                    self.expired += 1
                if not dq:
                    del self.by_user[uid]
            # stalled = failed events with no strictly-later in-window success (same user)
            result = {}
            for dq in self.by_user.values():
                last_success = None
                for ts, status, _ in dq:
                    if status == "success":
                        last_success = ts
                for ts, status, rid in dq:
                    if status != "success" and (last_success is None or last_success <= ts):
                        result[rid] = None
            yield now, result
