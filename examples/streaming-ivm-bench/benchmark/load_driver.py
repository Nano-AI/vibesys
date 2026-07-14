"""Load driver (TASKS.md 1.8): replay events.csv into the shared ingestion substrate.

The FAIR substrate every live engine reads from (CONTRACT.md §1): the same seeded events,
same JSON encoding, same key (`user_id`), emitted in non-decreasing event-time order. A
wall-clock `ingest_ts` is stamped on every message so the results-side consumer can measure
end-to-end latency (marker protocol, CONTRACT.md §7) later.

Transports:
  * --to kafka : produce to Kafka topic EVENTS_TOPIC (needs `kafka-python` + a broker; see
                 docker-compose.yml). Creates the topic with the right partition count.
  * --to file  : write events.jsonl (identical records) -- lets the whole pipeline be
                 exercised offline, and is what the oracle/offline engines consume.

Pacing:
  * --asap        : as fast as possible (throughput ceiling / saturation probing).
  * --rate R      : fixed R events/sec (wall clock).
  * --speed X     : replay at X* event-time (X=1 => real time; preserves burst shape).

Usage (from the example root):
  python3 benchmark/load_driver.py --to file --asap
  python3 benchmark/load_driver.py --to kafka --speed 1 --partitions 1     # correctness run
  python3 benchmark/load_driver.py --to kafka --asap  --partitions 8       # throughput run
"""

import argparse
import csv
import json
import os
import sys
import time

# config (topic names, partitions, EVENTS_CSV) lives in the reference slot's core/.
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference", "core")
)

import config  # noqa: E402

_INT = ("input_tokens", "output_tokens", "reasoning_tokens", "latency_ms")


def _records(path):
    with open(path) as f:
        for row in csv.DictReader(f):
            row["ts"] = float(row["ts"])
            for k in _INT:
                row[k] = int(row[k])
            row["cost_usd"] = float(row["cost_usd"])
            yield row


def _paced(records, mode, rate, speed):
    """Yield (record, sleep_until_offset_s) honoring the pacing mode. offset is wall-seconds
    from start at which the record should be emitted (None => asap)."""
    t0_event = None
    for i, rec in enumerate(records):
        if mode == "asap":
            yield rec, None
        elif mode == "rate":
            yield rec, i / rate
        else:  # speed: align wall time to event time / speed
            if t0_event is None:
                t0_event = rec["ts"]
            yield rec, (rec["ts"] - t0_event) / speed


def _make_producer(bootstrap, partitions, topic):
    from kafka import KafkaProducer  # import-time optional dep (kafka-python)
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError

    admin = KafkaAdminClient(bootstrap_servers=bootstrap)
    try:
        admin.create_topics([NewTopic(name=topic, num_partitions=partitions, replication_factor=1)])
    except TopicAlreadyExistsError:
        pass
    finally:
        admin.close()
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        key_serializer=lambda k: k.encode(),
        value_serializer=lambda v: json.dumps(v).encode(),
        linger_ms=5,
    )


def run(to, mode, rate, speed, partitions, events_csv, bootstrap, out_file):
    recs = _records(events_csv or config.EVENTS_CSV)
    sink = None
    fh = None
    if to == "kafka":
        try:
            sink = _make_producer(bootstrap, partitions, config.EVENTS_TOPIC)
        except ImportError:
            raise SystemExit(
                "kafka-python not installed. `pip install kafka-python` and start the "
                "broker (docker-compose up -d kafka), or use --to file."
            )
    else:
        fh = open(out_file, "w")

    n = 0
    start = time.perf_counter()
    for rec, offset in _paced(recs, mode, rate, speed):
        if offset is not None:
            dt = start + offset - time.perf_counter()
            if dt > 0:
                time.sleep(dt)
        rec = dict(rec)
        rec["ingest_ts"] = time.time()  # wall clock stamp for e2e latency
        if to == "kafka":
            sink.send(config.EVENTS_TOPIC, key=rec["user_id"], value=rec)
        else:
            fh.write(json.dumps(rec) + "\n")
        n += 1
    if sink:
        sink.flush()
        sink.close()
    if fh:
        fh.close()
    elapsed = time.perf_counter() - start
    print(
        f"emitted {n} events to {to} in {elapsed:.2f}s "
        f"({n / elapsed:,.0f} eps) mode={mode} partitions={partitions}"
    )
    return n


def main():
    ap = argparse.ArgumentParser(description="Replay events into Kafka or a file.")
    ap.add_argument("--to", choices=["kafka", "file"], default="file")
    pace = ap.add_mutually_exclusive_group()
    pace.add_argument("--asap", action="store_const", dest="mode", const="asap")
    pace.add_argument("--rate", type=float, help="fixed events/sec")
    pace.add_argument("--speed", type=float, help="replay at X* event-time")
    ap.add_argument("--partitions", type=int, default=config.EVENTS_PARTITIONS_CORRECTNESS)
    ap.add_argument("--events", default=None)
    ap.add_argument("--bootstrap", default=config.KAFKA_BOOTSTRAP)
    ap.add_argument("--out-file", default="events.jsonl")
    args = ap.parse_args()

    mode = args.mode or ("rate" if args.rate else "speed" if args.speed else "asap")
    run(
        args.to,
        mode,
        args.rate or 0.0,
        args.speed or 1.0,
        args.partitions,
        args.events,
        args.bootstrap,
        args.out_file,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
