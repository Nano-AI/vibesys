"""Query registry: the single place that ties each query's SQL (the oracle definition),
its snapshot parameters, and its exact reference maintainer together.

Every engine under test realizes these queries; the oracle (oracle.py) runs the SQL per
snapshot to define truth, and the matching reference maintainer proves the harness is
exact. Adding a query means adding one entry here + one `.sql` + one maintainer -- no new
event data (the generator schema already carries model / status / cost inputs).

    key_col   : the grouping key column in the SQL result (contract §5)
    value_col : the aggregate column, or None for pure-membership queries (Q4)
"""

import os

import config
from maintainer import MeteringMaintainer
from maintainers import (
    DistinctUsersMaintainer,
    StalledRequestsMaintainer,
    TopKCostMaintainer,
)

_QDIR = os.path.join(os.path.dirname(__file__), "queries")


def _load(fname):
    with open(os.path.join(_QDIR, fname)) as f:
        return f.read()


class Query:
    def __init__(self, name, title, sql_file, params_fn, maintainer, key_col, value_col):
        self.name = name
        self.title = title
        self.sql = _load(sql_file)
        self._params_fn = params_fn
        self.maintainer = maintainer  # class; instantiate per run
        self.key_col = key_col
        self.value_col = value_col  # None => membership-only

    def params(self, now):
        return self._params_fn(now)


def _base(now):
    return {"now": now, "window": config.WINDOW_SECONDS}


REGISTRY = {
    "metering": Query(
        "metering",
        "Q1 token metering / quota",
        "metering.sql",
        lambda now: {**_base(now), "budget": config.BUDGET_TOKENS},
        MeteringMaintainer,
        "user_id",
        "windowed_tokens",
    ),
    "active_users": Query(
        "active_users",
        "Q2 active-user cardinality",
        "active_users.sql",
        _base,
        DistinctUsersMaintainer,
        "project_id",
        "active_users",
    ),
    "top_cost": Query(
        "top_cost",
        "Q3 top-k costliest models",
        "top_cost.sql",
        lambda now: {**_base(now), "k": config.TOP_K},
        TopKCostMaintainer,
        "model",
        "cost_micro",
    ),
    "stalled": Query(
        "stalled",
        "Q4 stalled requests (anti-join)",
        "stalled.sql",
        _base,
        StalledRequestsMaintainer,
        "request_id",
        None,
    ),
}

ALL = list(REGISTRY)


def get(name):
    if name not in REGISTRY:
        raise KeyError(f"unknown query {name!r}; known: {ALL}")
    return REGISTRY[name]
