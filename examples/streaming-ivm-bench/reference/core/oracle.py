"""Ground-truth oracle: DuckDB batch recompute, one snapshot at a time, per query.

This defines THE ONE CORRECT ANSWER for every query in the registry. At each snapshot
event-time `t` it runs the query's SQL over all events with ts <= t, with `now()` bound to
`t` (never wall-clock). The sequence of result sets is the reference changelog that every
engine under test -- Flink, RisingWave, and the bespoke engine alike -- is scored against
(accuracy is a measured, symmetric metric; see DESIGN.md §5-6.3).

The oracle is not itself a system under test and is not tuned to any engine's window
model; it is the neutral definition of truth. Result form is uniform across queries:
`{key: value}` (value is None for pure-membership queries such as the Q4 anti-join),
matching the accuracy scorer and the I/O contract (CONTRACT.md §3-5).
"""

import config
import duckdb
import queries


class Oracle:
    def __init__(self, query="metering", events_csv=None):
        """`query` is a registry name (queries.REGISTRY) or a Query object."""
        self.query = queries.get(query) if isinstance(query, str) else query
        events_csv = events_csv or config.EVENTS_CSV
        self.con = duckdb.connect(":memory:")
        self.con.execute("CREATE TABLE events AS SELECT * FROM read_csv_auto(?)", [events_csv])

    def snapshot(self, now):
        """Return the correct result {key: value} at event-time `now`.

        value is int for aggregate queries, None for membership-only queries.
        """
        rows = self.con.execute(self.query.sql, self.query.params(now)).fetchall()
        if self.query.value_col is None:
            return {str(row[0]): None for row in rows}
        return {str(row[0]): int(row[1]) for row in rows}

    def close(self):
        self.con.close()
