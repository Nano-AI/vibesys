# Reproduce the RisingWave baseline natively (no Docker) — step by step

This is the exact procedure used to capture the RisingWave Q1–Q4 **correctness** numbers,
runnable end-to-end by you with no root and no Docker. (Docker was unavailable on the test
machine — daemon locked to root, no sudo — so we run Kafka and RisingWave as plain user
processes. If you *do* have Docker, `../../docker-compose.yml` + this dir's `README.md` is
the containerized equivalent.)

Everything lives under `streaming-ivm-bench/.native/`. Delete that dir to remove all of it.

> **Shell note:** run the commands below in **bash**, not zsh. zsh does not word-split
> unquoted variables, which silently breaks `$KAFKA ... ` style command shortcuts. The
> helper script has a `#!/usr/bin/env bash` shebang for this reason.

---

## 0. Prerequisites (already true on the test box)

| need | check | notes |
|------|-------|-------|
| Java 17+ | `java -version` | for Kafka |
| `psql` | `psql --version` | Postgres client, talks to RisingWave on :4566 |
| Python + duckdb | `python3 -c "import duckdb"` | the oracle |
| `kafka-python` | `python3 -c "import kafka"` | `pip install kafka-python` if missing |
| ~1.2 GB disk, ~3 GB RAM | — | downloads + running processes |

From here on, `$BENCH` = the `streaming-ivm-bench` directory:

```bash
BENCH=/home/haoqingxuan/vibe-database/vibe-serve/examples/streaming-ivm-bench   # adjust to your path
cd "$BENCH" && mkdir -p .native
```

---

## 1. One-time downloads (~370 MB)

```bash
cd "$BENCH/.native"

# Kafka 3.9.0
wget -q https://archive.apache.org/dist/kafka/3.9.0/kafka_2.13-3.9.0.tgz -O kafka.tgz
tar xzf kafka.tgz && mv kafka_2.13-3.9.0 kafka && rm kafka.tgz

# RisingWave v2.1.0 (all-in-one single-node binary)
wget -q https://github.com/risingwavelabs/risingwave/releases/download/v2.1.0/risingwave-v2.1.0-x86_64-unknown-linux-all-in-one.tar.gz -O rw.tgz
mkdir -p risingwave && tar xzf rw.tgz -C risingwave && rm rw.tgz
```

---

## 2. Start the infrastructure

### Kafka (single-node KRaft, all on localhost)

```bash
cd "$BENCH/.native"
cat > server.properties <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@localhost:9093
listeners=PLAINTEXT://:9092,CONTROLLER://:9093
advertised.listeners=PLAINTEXT://localhost:9092
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
inter.broker.listener.name=PLAINTEXT
log.dirs=$BENCH/.native/kraft-logs
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
auto.create.topics.enable=true
num.partitions=1
EOF

KID=$(kafka/bin/kafka-storage.sh random-uuid)
kafka/bin/kafka-storage.sh format -t "$KID" -c server.properties
nohup kafka/bin/kafka-server-start.sh server.properties > kafka.log 2>&1 &

# wait until it answers
until kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1; do sleep 2; done
echo "Kafka up"
```

### RisingWave (single-node, in-memory = clean state each start)

```bash
cd "$BENCH/.native/risingwave"
nohup ./risingwave single_node --in-memory > ../risingwave.log 2>&1 &

# wait for the SQL port
until psql -h localhost -p 4566 -d dev -U root -tAc "SELECT 1;" >/dev/null 2>&1; do sleep 2; done
echo "RisingWave up"
```

---

## 3. Run all four queries and grade them

One command (idempotent — resets objects + topics, defines all four MVs, loads once, grades):

```bash
cd "$BENCH"
bash baselines/risingwave/run_native.sh
```

That script does exactly the manual steps below, if you prefer to run them by hand:

1. **Reset** RisingWave objects + Kafka topics (fresh offsets).
2. **Define ALL four queries before loading any data** — critical: a RisingWave `SOURCE`
   only feeds MVs created before the data arrives; defining every MV up front guarantees
   each sees the full stream. The baseline SQL uses `kafka:9092` (container name); rewrite
   it to `localhost:9092` for native:
   ```bash
   cat baselines/risingwave/{metering,active_users,top_cost,stalled}.sql \
     | sed 's/kafka:9092/localhost:9092/g' > .native/all_queries.native.sql
   psql -h localhost -p 4566 -d dev -U root -v ON_ERROR_STOP=1 -f .native/all_queries.native.sql
   ```
   (The `timezone is UTC` NOTICE is harmless — `extract(epoch …)` is absolute, so
   `snapshot_ts` still lands on `t_k`.)
3. **Load the stream once** (correctness run: replay at 50× event-time into 1 partition):
   ```bash
   python3 benchmark/load_driver.py --to kafka --speed 50 --partitions 1 --bootstrap localhost:9092
   ```
4. **Dump + grade** each query (the `--bootstrap localhost:9092` override matters — the
   default in `reference/core/config.py` is the container port `29092`):
   ```bash
   for q in metering active_users top_cost stalled; do
     python3 accuracy_checker/dump_results.py --query $q --out .native/rw_$q.jsonl --bootstrap localhost:9092 --timeout 8
     python3 accuracy_checker/checker.py      --query $q --engine-output .native/rw_$q.jsonl
     python3 benchmark/results.py --add risingwave $q .native/rw_$q.jsonl   # merge into results.md
   done
   ```

---

## 4. Expected results (RisingWave v2.1.0, seed 42, W=60s, S=1s)

| query | exact-match | F1 | value MAE | reading |
|-------|-------------|----|-----------|---------|
| `metering`     | **1.0000** (301/301) | 1.0000 | 0.00 | windowed SUM + HAVING + retraction: exact |
| `active_users` | **1.0000** (301/301) | 1.0000 | 0.00 | exact COUNT(DISTINCT) (pays in state, not accuracy) |
| `top_cost`     | **0.9934** (299/301) | 1.0000 | 12903.65 | 2 snapshots differ by one boundary event ($5.83) |
| `stalled`      | **0.0465** (14/301)  | 0.0465 | 0.00 | windowed anti-join not snapshot-aligned in plain SQL |

**Determinism caveat:** the *settled* accuracy above is stable across runs. The raw changelog
volume is NOT — `dump_results` may report different upsert/delete/orphan counts run to run
(RisingWave's transient Top-N churn and future-window emission are timing-dependent). The
grader settles the changelog (last-op-wins per `(snapshot_ts, key)`), so the graded numbers
don't move. If your *settled* numbers differ from the table, that's a real finding worth
investigating; if only the raw counts differ, that's expected churn.

**Why `top_cost` is 0.9934, not 1.0:** RisingWave HOP windows are half-open `[start, end)`;
the oracle observable is `(t_k − W, t_k]`. A single event sitting exactly on a snapshot
boundary is counted by one and not the other, shifting the windowed cost at 2 snapshots.
This is a genuine window-semantics difference and is **counted against RisingWave** — we do
not align the grid to its window model.

**Why `stalled` is 0.0465:** the Q4 SQL (`baselines/risingwave/stalled.sql`) emits one row
per stalled request stamped with the event's own `ts` (off the snapshot grid), and reflects
only the end-state "currently stalled" set — not the per-snapshot answer. A faithful
realization needs more than SQL (a keyed timer / MATCH_RECOGNIZE). This is the documented
hard case: the windowed anti-join is exactly where general streaming SQL struggles.

---

## 5. Throughput / latency (separate run — not captured above)

The above is the **correctness** run. For throughput/saturation, load at full speed into 8
partitions and watch the sink lag; end-to-end latency uses the marker protocol
(`../../CONTRACT.md` §7). Not yet done for RisingWave.

```bash
python3 benchmark/load_driver.py --to kafka --asap --partitions 8 --bootstrap localhost:9092
```

---

## 6. Teardown

```bash
# stop RisingWave (kill the parent; the all-in-one respawns children until the top proc dies)
pkill -9 -f 'risingwave single_node'
# stop Kafka
"$BENCH/.native/kafka/bin/kafka-server-stop.sh"
# remove everything
rm -rf "$BENCH/.native"
```

---

## 7. Troubleshooting notes (things that bit us)

- **zsh vs bash:** command-shortcut variables (`K="kafka/bin/..."; $K --list`) silently fail
  in zsh (no word-split) and look like "no topics." Use bash, or inline the full path.
- **Kafka topic deletion is async:** `--delete` then immediate `--create` races. Wait ~8 s.
- **Tombstones = retractions:** upsert sinks emit a null-payload Kafka message to retract a
  `(snapshot_ts, key)`. `dump_results.py` writes these as explicit `_deleted` records and the
  checker settles last-op-wins. Do **not** drop tombstones — an insert→tombstone pair would
  wrongly settle as still-present (this bug once made RisingWave's `top_cost` look worse than
  it is; catching it is why the number moved from 0.9834 to 0.9934).
- **RisingWave respawns on partial kill:** the all-in-one supervises child processes; kill
  the top `risingwave single_node` process (`pkill -9 -f`), not a child.
- **`--bootstrap localhost:9092`** is required for native runs; `config.py` defaults to the
  container-path port `29092`.
