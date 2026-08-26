from itertools import pairwise
from pathlib import Path

from vibesys.domains.base import DomainName
from vibesys.input_manifest import InputBundle, load_input_bundle

PROJECT_ROOT = Path(__file__).parents[1]
DIFFERENTIAL_DATAFLOW_ROOT = (
    PROJECT_ROOT / "examples" / "database" / "differential-dataflow-cpu-bench"
)


def _bundle() -> InputBundle:
    return load_input_bundle(DIFFERENTIAL_DATAFLOW_ROOT)


def _adjacent_pairs(command: tuple[str, ...]) -> set[tuple[str, str]]:
    return set(pairwise(command))


def test_differential_dataflow_bundle_selects_database_domain() -> None:
    bundle = _bundle()

    assert bundle.root == DIFFERENTIAL_DATAFLOW_ROOT.resolve()
    assert bundle.domain is DomainName.DATABASE


def test_accuracy_runs_the_equivalence_gate_against_the_editable_engine() -> None:
    bundle = _bundle()

    assert bundle.accuracy_command[:4] == (
        "uv",
        "run",
        "python",
        "accuracy_checker/equivalence_gate.py",
    )
    pairs = _adjacent_pairs(bundle.accuracy_command)
    assert ("--engine-cmd", "engine/target/release/examples/bfs") in pairs
    # The gate rebuilds the candidate engine in place before grading it.
    assert "--rebuild-cmd" in bundle.accuracy_command


def test_benchmark_reports_the_cpu_reduction_ratio() -> None:
    bundle = _bundle()

    assert bundle.benchmark_command[:4] == (
        "uv",
        "run",
        "python",
        "benchmark/benchmark.py",
    )
    assert ("--engine-cmd", "engine/target/release/examples/bfs") in _adjacent_pairs(
        bundle.benchmark_command
    )
    assert bundle.benchmark_result is not None
    assert bundle.benchmark_result.json_argument == "--output-json"
    assert bundle.benchmark_result.metric == "cpu_reduction_ratio"


def test_bundle_vendors_both_the_candidate_and_pristine_engine() -> None:
    # The candidate the agent edits in place, and the pristine round-0 snapshot the
    # equivalence gate runs live to produce the golden output, are both vendored so
    # the target is self-contained and offline.
    assert (DIFFERENTIAL_DATAFLOW_ROOT / "engine" / "Cargo.toml").is_file()
    assert (DIFFERENTIAL_DATAFLOW_ROOT / "_ref_engine" / "Cargo.toml").is_file()
    # The heavyweight build tree must never be vendored.
    assert not (DIFFERENTIAL_DATAFLOW_ROOT / "engine" / "target").exists()
    assert not (DIFFERENTIAL_DATAFLOW_ROOT / "_ref_engine" / "target").exists()
