package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"vibesys/microservice-evaluator/api"
	"vibesys/microservice-evaluator/engine"
)

// trainTicketObjective is the objective the committed Train Ticket workload
// declares. Its name and its metric differ on purpose: the stream declares the
// metric, which is the quantity the summary measures.
func trainTicketObjective() api.Objective {
	return api.Objective{
		Name:      "logical_operations_per_second",
		Metric:    "operations_per_second",
		Direction: "maximize",
		Unit:      "operations/s",
	}
}

func measuredSummary(objective api.Objective, value float64) engine.Summary {
	return engine.Summary{
		SchemaVersion: engine.ResultSchemaVersion,
		WorkloadName:  "train-ticket",
		PrimaryValue:  &value,
		PrimaryMetric: objective,
		Valid:         true,
		Aggregate:     engine.Aggregate{Trials: 3, Median: &value},
	}
}

func readStreamRecords(t *testing.T, path string) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	records := make([]map[string]any, 0, 2)
	for _, line := range strings.Split(string(data), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			t.Fatalf("record %q: %v", line, err)
		}
		records = append(records, record)
	}
	return records
}

// summaryPrimaryValue reads primary_value out of the summary exactly as the
// --output-json report serializes it, so the stream can be compared against
// the number the diagnostic report publishes rather than against the Go field.
func summaryPrimaryValue(t *testing.T, summary engine.Summary) any {
	t.Helper()
	encoded, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	var report map[string]any
	if err := json.Unmarshal(encoded, &report); err != nil {
		t.Fatal(err)
	}
	return report["primary_value"]
}

func TestBenchmarkStreamDeclaresWorkloadObjective(t *testing.T) {
	objective := trainTicketObjective()
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(objective, streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()

	summary := measuredSummary(objective, 412.5)
	if err := stream.emit(summary); err != nil {
		t.Fatal(err)
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 {
		t.Fatalf("stream records = %v, want hello and result", records)
	}
	hello := records[0]
	if hello["kind"] != "hello" || hello["protocol"] != float64(1) {
		t.Fatalf("hello record = %v", hello)
	}
	metrics, ok := hello["metrics"].(map[string]any)
	if !ok || len(metrics) != 1 {
		t.Fatalf("hello metrics = %v, want exactly the workload objective metric", hello["metrics"])
	}
	spec, ok := metrics[objective.Metric].(map[string]any)
	if !ok {
		t.Fatalf("hello metrics = %v, want %q", metrics, objective.Metric)
	}
	if spec["unit"] != objective.Unit || spec["direction"] != "max" {
		t.Fatalf("%s spec = %v", objective.Metric, spec)
	}

	result := records[1]
	if result["kind"] != "result" || result["label"] != "" {
		t.Fatalf("result record = %v", result)
	}
	values, ok := result["values"].(map[string]any)
	if !ok || len(values) != 1 {
		t.Fatalf("result values = %v, want exactly %q", result["values"], objective.Metric)
	}
	if values[objective.Metric] != summaryPrimaryValue(t, summary) {
		t.Fatalf(
			"streamed %s = %v, summary primary_value = %v",
			objective.Metric,
			values[objective.Metric],
			summaryPrimaryValue(t, summary),
		)
	}
}

func TestBenchmarkStreamDeclaresMinimizedObjective(t *testing.T) {
	objective := api.Objective{
		Name:      "read_latency_p50",
		Metric:    "latency_ms.p50",
		Direction: "minimize",
		Unit:      "ms",
	}
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(objective, streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()
	if err := stream.emit(measuredSummary(objective, 12.5)); err != nil {
		t.Fatal(err)
	}

	records := readStreamRecords(t, streamPath)
	metrics, ok := records[0]["metrics"].(map[string]any)
	if !ok {
		t.Fatalf("hello record = %v", records[0])
	}
	spec, ok := metrics[objective.Metric].(map[string]any)
	if !ok {
		t.Fatalf("hello metrics = %v, want %q", metrics, objective.Metric)
	}
	if spec["unit"] != "ms" || spec["direction"] != "min" {
		t.Fatalf("%s spec = %v", objective.Metric, spec)
	}
}

func TestStartBenchmarkStreamRejectsUnknownDirection(t *testing.T) {
	objective := trainTicketObjective()
	objective.Direction = "sideways"
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	if _, err := startBenchmarkStream(objective, streamPath); err == nil {
		t.Fatal("accepted an objective direction the protocol cannot declare")
	}
	if _, err := os.Stat(streamPath); err == nil {
		t.Fatal("rejected objective still opened a stream")
	}
}

func TestBenchmarkStreamReportsInvalidResultAsErrorRecord(t *testing.T) {
	objective := trainTicketObjective()
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(objective, streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()

	summary := measuredSummary(objective, 412.5)
	summary.Valid = false
	summary.PrimaryValue = nil
	summary.Constraints = engine.ConstraintResult{
		Passed:  false,
		Reasons: []string{"trial 0: error rate 0.02 exceeds 0.00"},
	}
	emitErr := stream.emit(summary)
	if emitErr == nil {
		t.Fatal("invalid summary was reported as a measurement")
	}
	// The command reports the failure the way it always has; the stream picks
	// the same reason up from the returned error.
	if got := stream.finish(emitErr); !errors.Is(got, emitErr) {
		t.Fatalf("finish(%v) = %v, want the cause unchanged", emitErr, got)
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
	if records[0]["kind"] != "hello" {
		t.Fatalf("first record = %v, want hello", records[0])
	}
	if records[1]["kind"] != "error" || records[1]["message"] != emitErr.Error() {
		t.Fatalf("second record = %v, want the error %q", records[1], emitErr)
	}
}

func TestBenchmarkStreamReportsAbsentPrimaryValueAsErrorRecord(t *testing.T) {
	objective := trainTicketObjective()
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(objective, streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()

	// A summary the engine accepted but that carries no aggregate value has no
	// row to report, and a Go zero must not pass for a measurement.
	summary := measuredSummary(objective, 0)
	summary.PrimaryValue = nil
	summary.Aggregate = engine.Aggregate{Trials: 3}
	emitErr := stream.emit(summary)
	if emitErr == nil {
		t.Fatal("summary without a primary value was reported as a measurement")
	}
	if !strings.Contains(emitErr.Error(), objective.Metric) {
		t.Fatalf("error = %v, want it to name %q", emitErr, objective.Metric)
	}
	if got := stream.finish(emitErr); !errors.Is(got, emitErr) {
		t.Fatalf("finish(%v) = %v, want the cause unchanged", emitErr, got)
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 || records[1]["kind"] != "error" {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
	if records[1]["message"] != emitErr.Error() {
		t.Fatalf("error record message = %v, want %q", records[1]["message"], emitErr)
	}
}

// TestBenchmarkStreamFinishReportsCommandFailure covers the failures that
// never reach emit: a candidate that does not start, a telemetry collector
// fault, a cleanup error on the way out. The deferred finish turns each into
// the stream's error record.
func TestBenchmarkStreamFinishReportsCommandFailure(t *testing.T) {
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(trainTicketObjective(), streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()

	cause := errors.New("prepare managed candidate: readiness probe timed out")
	if got := stream.finish(cause); !errors.Is(got, cause) {
		t.Fatalf("finish(%v) = %v, want the cause unchanged", cause, got)
	}
	// A second failure on the way out must not append a second outcome record.
	if got := stream.finish(errors.New("close managed candidate: exit status 1")); got == nil {
		t.Fatal("finish swallowed a later failure")
	}

	records := readStreamRecords(t, streamPath)
	if len(records) != 2 {
		t.Fatalf("stream records = %v, want hello and error", records)
	}
	if records[1]["kind"] != "error" || records[1]["message"] != cause.Error() {
		t.Fatalf("error record = %v, want %q", records[1], cause)
	}
}

// TestBenchmarkStreamAfterEmitKeepsTheResult guards the ordering of the
// deferred finish against the deferred managed-candidate cleanup: a cleanup
// failure after a measured row must not overwrite the row with an error.
func TestBenchmarkStreamAfterEmitKeepsTheResult(t *testing.T) {
	objective := trainTicketObjective()
	streamPath := filepath.Join(t.TempDir(), "stream.jsonl")

	stream, err := startBenchmarkStream(objective, streamPath)
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()
	if err := stream.emit(measuredSummary(objective, 412.5)); err != nil {
		t.Fatal(err)
	}

	cause := errors.New("close managed candidate: exit status 1")
	if got := stream.finish(cause); !errors.Is(got, cause) {
		t.Fatalf("finish(%v) = %v, want the cause unchanged", cause, got)
	}
	records := readStreamRecords(t, streamPath)
	if len(records) != 2 || records[1]["kind"] != "result" {
		t.Fatalf("stream records = %v, want hello and result", records)
	}
}

func TestBenchmarkWithoutStreamFlagWritesNothing(t *testing.T) {
	objective := trainTicketObjective()
	outputs := t.TempDir()

	stream, err := startBenchmarkStream(objective, "")
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()
	if stream.run.Reporting() {
		t.Fatal("omitting the output path still reports a stream")
	}
	if err := stream.emit(measuredSummary(objective, 412.5)); err != nil {
		t.Fatal(err)
	}
	cause := errors.New("benchmark result is invalid")
	if got := stream.finish(cause); !errors.Is(got, cause) {
		t.Fatalf("finish(%v) = %v, want the cause unchanged", cause, got)
	}

	entries, err := os.ReadDir(outputs)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 0 {
		t.Fatalf("omitting the output path wrote files: %v", entries)
	}
}
