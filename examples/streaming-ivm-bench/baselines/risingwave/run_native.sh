#!/usr/bin/env bash
# Reproduce the RisingWave correctness run for all four queries, natively (no Docker).
# Assumes Kafka + RisingWave are already running (see RUN_NATIVE.md "Start infra").
# Idempotent: it drops/recreates RisingWave objects and resets topics each time.
#
# Usage:  bash baselines/risingwave/run_native.sh
#
# bash (NOT zsh) on purpose: this script relies on unquoted word-splitting.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE="$REPO/.native"
KT="$NATIVE/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092"
PSQL="psql -h localhost -p 4566 -d dev -U root"
QUERIES="metering active_users top_cost stalled"
cd "$REPO"

echo "== 1. reset RisingWave objects (drop source cascades to MVs + sinks) =="
$PSQL -c "DROP SOURCE IF EXISTS events CASCADE;" >/dev/null 2>&1 || true

echo "== 2. reset Kafka topics (fresh offsets) =="
for t in llm-events results.metering results.active_users results.top_cost results.stalled; do
  $KT --delete --topic "$t" >/dev/null 2>&1 || true
done
sleep 8   # topic deletion is async in Kafka
for t in llm-events results.metering results.active_users results.top_cost results.stalled; do
  $KT --create --topic "$t" --partitions 1 --replication-factor 1 >/dev/null 2>&1
done

echo "== 3. define ALL queries BEFORE loading data (so every MV sees the full stream) =="
cat baselines/risingwave/metering.sql \
    baselines/risingwave/active_users.sql \
    baselines/risingwave/top_cost.sql \
    baselines/risingwave/stalled.sql \
  | sed 's/kafka:9092/localhost:9092/g' > "$NATIVE/all_queries.native.sql"
$PSQL -v ON_ERROR_STOP=1 -f "$NATIVE/all_queries.native.sql" >/dev/null

echo "== 4. load the stream once (correctness run: speed 50, single partition) =="
python3 benchmark/load_driver.py --to kafka --speed 50 --partitions 1 --bootstrap localhost:9092 2>/dev/null | grep emitted

echo "== 5. let RisingWave settle, then dump + grade each query =="
sleep 8
for q in $QUERIES; do
  python3 accuracy_checker/dump_results.py --query "$q" --out "$NATIVE/rw_$q.jsonl" --bootstrap localhost:9092 --timeout 8 2>/dev/null | grep wrote
  echo "----- $q -----"
  python3 accuracy_checker/checker.py --query "$q" --engine-output "$NATIVE/rw_$q.jsonl" 2>/dev/null \
    | grep -E "exact-match|precision|value MAE|orphan"
  python3 benchmark/results.py --add risingwave "$q" "$NATIVE/rw_$q.jsonl" >/dev/null 2>&1
done

echo "== done. Table updated in results.md / results.json =="
