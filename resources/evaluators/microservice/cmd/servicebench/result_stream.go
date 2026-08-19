package main

import (
	"errors"
	"fmt"

	"github.com/uw-syfi/vibesys/sdk/vs-evaluator/vseval"

	"vibesys/microservice-evaluator/api"
	"vibesys/microservice-evaluator/engine"
)

// benchmarkStream is the framework-facing output of benchmark mode: the
// VibeSys evaluator record stream described by sdk/vs-evaluator/PROTOCOL.md.
// It declares the workload's own objective metric and carries either the
// summary's primary value or the reason no value exists.
//
// The stream reports one metric because the summary has one optimization
// score. Everything else the summary holds (per-trial distributions, generator
// health, telemetry) stays in the --output-json report, which is diagnostics
// rather than a measurement contract.
//
// Without --vs-output the SDK run discards every record, so standalone
// invocations that only want the printed summary and the --output-json report
// behave exactly as before.
type benchmarkStream struct {
	run     *vseval.Run
	metric  vseval.Metric
	emitted bool
}

// startBenchmarkStream declares the workload's objective metric and writes the
// hello record to outputPath, or to nothing when outputPath is empty.
//
// The declared name, unit, and direction are the workload's own: an objective
// with metric "operations_per_second" reaches the framework under that name
// rather than under a generic result field. The metric therefore cannot be
// declared before the workload is loaded, which is why the hello record lands
// after configuration rather than at flag-parse time. It still lands before
// any measurement runs, so a crashed or timed-out benchmark leaves a stream
// that names its metric.
func startBenchmarkStream(objective api.Objective, outputPath string) (*benchmarkStream, error) {
	direction, err := streamDirection(objective.Direction)
	if err != nil {
		return nil, err
	}
	schema := vseval.NewSchema()
	metric := schema.Number(
		objective.Metric,
		vseval.Unit(objective.Unit),
		vseval.Direction(direction),
	)
	run, err := schema.StartWith(outputPath)
	if err != nil {
		return nil, err
	}
	return &benchmarkStream{run: run, metric: metric}, nil
}

// streamDirection maps a workload objective direction onto the protocol's
// advisory better-direction. config.Validate already rejects anything else;
// this fails closed rather than declaring a metric with no direction.
func streamDirection(direction string) (vseval.Dir, error) {
	switch direction {
	case "maximize":
		return vseval.Max, nil
	case "minimize":
		return vseval.Min, nil
	default:
		return "", fmt.Errorf("objective.direction must be minimize or maximize, got %q", direction)
	}
}

// emit writes the summary's primary value as the stream's result record.
//
// A run the engine rejected, or one that produced no primary value, is a
// failure rather than a measurement: emit returns the reason and leaves the
// error record to finish, so the command's exit status and stderr keep the
// wording they had before the stream existed.
func (s *benchmarkStream) emit(summary engine.Summary) error {
	if !summary.Valid {
		return errors.New("benchmark result is invalid; inspect constraints and trial invalid_reasons")
	}
	if summary.PrimaryValue == nil {
		return fmt.Errorf("benchmark produced no %s value", summary.PrimaryMetric.Metric)
	}
	s.run.Set(s.metric, *summary.PrimaryValue)
	if err := s.run.Emit(); err != nil {
		return err
	}
	s.emitted = true
	return nil
}

// finish reports cause as the stream's error record and returns it unchanged.
// Deferring it covers every failure the command can return once the stream is
// open, including the ones managed-candidate cleanup adds on the way out,
// without each return site having to know about the stream.
//
// A command that returns nothing and reported nothing leaves a stream holding
// only its hello record, which a reader reports as a missing outcome. That is
// the honest report: no measurement was made.
func (s *benchmarkStream) finish(cause error) error {
	if cause == nil || s.emitted {
		return cause
	}
	if err := s.run.EmitError(cause); err != nil {
		return fmt.Errorf("%w (reporting the failure also failed: %v)", cause, err)
	}
	s.emitted = true
	return cause
}

// Close releases the output file. It is the only close in the command: the SDK
// leaves it to whoever started the run.
func (s *benchmarkStream) Close() error {
	return s.run.Close()
}
