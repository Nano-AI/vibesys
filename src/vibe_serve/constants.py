from enum import StrEnum
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = ".agents/skills/"

# ANSI colors
_DIM = "\033[2m"
_RED = "\033[31m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RESET = "\033[0m"

_ANTHROPIC_PREFIXES = ("claude-",)
_GOOGLE_PREFIXES = ("gemini-", "gemma-")
_OPENAI_PREFIXES = ("gpt-", "o1", "o3", "o4")


class ComputeBackend(StrEnum):
    """Compute backends the agent can target.

    Add a new variant here when a compute stack (sandbox image,
    device selection, profiler) is wired up end-to-end.

    - ``CPU`` is a native-CPU / compiled-engine target (no GPU/CUDA): the
      candidate is a compiled binary (e.g. ``cargo build --release``) run
      as a subprocess, not a torch model. Local-exec only for now —
      ``CpuBackend.make_sandbox`` raises on Docker/Modal (a portable
      CPU-container branch is a future hook). Performance is measured by
      the example's benchmark harness, not a GPU profiler.
    """

    CPU = "cpu"


DEFAULT_COMPUTE_BACKEND = ComputeBackend.CPU
KNOWN_COMPUTE_BACKENDS: tuple[str, ...] = tuple(b.value for b in ComputeBackend)

# Agent backend used when neither the ``--agent-backend`` flag nor an
# ``[agent].backend`` config key is set. Resolved in a single place so
# build_agent_runner and ComputeContext cannot drift.
DEFAULT_AGENT_BACKEND = "cli"
