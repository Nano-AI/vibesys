"""Dump a live engine's results topic to a JSONL file for the checker (TASKS.md 1.8/1.6).

The checker grades a FILE of result records; a Kafka `RESULTS_TOPIC.<query>` is turned into
that file here. Handles the SETTLED semantics of upsert sinks (CONTRACT.md §3):

  * A Kafka TOMBSTONE (null message value) is a RETRACTION of that (snapshot_ts, key). It is
    written out as an explicit DELETE record ({"snapshot_ts", "key", "_deleted": true}),
    carrying the primary key from the Kafka message KEY. The checker's last-wins settling
    then correctly collapses insert -> retract (-> re-insert) in output order: if the last
    op for a (snapshot_ts, key) is a delete, the key is absent at that snapshot.
    (Dropping tombstones silently is WRONG: an insert->tombstone pair would wrongly settle
    as still-present. This bit us once; do not "optimize" it back.)
  * Non-tombstone records are written through verbatim with their value field (which may be
    null for Q4's pure-membership rows -- distinct from a tombstone, which has a null Kafka
    message payload, not a null `value` field).

Needs `kafka-python` + a broker. Offline engines write the file directly and skip this.

Usage:
  python3 dump_results.py --query metering --out flink_metering.jsonl --timeout 10
"""

import argparse
import json
import os
import sys

# config (topic names, bootstrap) lives in the reference slot's core/.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "core")
)

import config  # noqa: E402


def dump(query, out_path, bootstrap, idle_timeout_s):
    from kafka import KafkaConsumer

    topic = f"{config.RESULTS_TOPIC_PREFIX}.{query}"
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=int(idle_timeout_s * 1000),
        key_deserializer=lambda b: b,  # raw; tombstones carry the PK in the key
        value_deserializer=lambda b: b,  # keep raw to detect tombstones (None)
    )
    n_upsert = n_delete = n_unplaceable = 0
    with open(out_path, "w") as f:
        for msg in consumer:
            if msg.value is None:  # Kafka tombstone => explicit DELETE
                if msg.key is None:
                    n_unplaceable += 1
                    continue
                k = json.loads(msg.key)
                if "snapshot_ts" not in k:  # PK lacks the snapshot dim; can't place it
                    n_unplaceable += 1
                    continue
                f.write(
                    json.dumps(
                        {
                            "snapshot_ts": float(k["snapshot_ts"]),
                            "key": str(k["key"]),
                            "_deleted": True,
                        }
                    )
                    + "\n"
                )
                n_delete += 1
                continue
            rec = json.loads(msg.value)
            # normalize field names the engine used to the contract's snapshot_ts/key/value
            f.write(
                json.dumps(
                    {
                        "snapshot_ts": float(rec.get("snapshot_ts", rec.get("window_end"))),
                        "key": str(rec["key"]),
                        "value": rec.get("value"),
                    }
                )
                + "\n"
            )
            n_upsert += 1
    consumer.close()
    print(
        f"wrote {n_upsert} upserts + {n_delete} deletes "
        f"({n_unplaceable} unplaceable tombstones skipped) -> {out_path}"
    )
    return n_upsert + n_delete


def main():
    ap = argparse.ArgumentParser(description="Dump a results topic to JSONL for the checker.")
    ap.add_argument("--query", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--bootstrap", default=config.KAFKA_BOOTSTRAP)
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="stop after this many seconds with no new records",
    )
    args = ap.parse_args()
    try:
        dump(args.query, args.out, args.bootstrap, args.timeout)
    except ImportError:
        raise SystemExit("kafka-python not installed. `pip install kafka-python`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
