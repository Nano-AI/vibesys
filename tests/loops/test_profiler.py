"""Tests for profiler integration: response models and agent-runner plumbing.

The orchestrate loop's profiler-gating behavior is covered in
``tests/test_orchestrate.py``; this module keeps the lower-level
ProfilerResponse / parser tests.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vibe_serve.agent_runner import (
    _parse_profiler_response_text,
    run_profiler_agent,
)
from vibe_serve.schemas import (
    ImplementerResponse,
    JudgeResponse,
    ProfilerResponse,
    Verdict,
)

# ---------------------------------------------------------------------------
# ProfilerResponse model tests
# ---------------------------------------------------------------------------


def test_profiler_response_creation():
    resp = ProfilerResponse(
        analysis="GPU is 85% busy, attention kernels dominate.",
        bottlenecks="1. flash_fwd_kernel (45% GPU time)\n2. rmsnorm_kernel (8%, 60 launches)",
        suggestions="Fuse RMSNorm kernels using FlashInfer ops.",
    )
    assert resp.analysis
    assert "flash_fwd_kernel" in resp.bottlenecks
    assert "FlashInfer" in resp.suggestions


def test_profiler_response_from_dict():
    data = {
        "analysis": "CPU launch overhead exceeds GPU exec time.",
        "bottlenecks": "Launch-bound: CPU/GPU ratio 1.7x",
        "suggestions": "Enable CUDA graphs for decode step.",
    }
    resp = ProfilerResponse.model_validate(data)
    assert resp.analysis == data["analysis"]


def test_profiler_response_serialization():
    resp = ProfilerResponse(
        analysis="Analysis.",
        bottlenecks="Bottlenecks.",
        suggestions="Suggestions.",
    )
    dumped = resp.model_dump()
    assert dumped["analysis"] == "Analysis."
    restored = ProfilerResponse.model_validate(dumped)
    assert restored == resp


# ---------------------------------------------------------------------------
# Profiler response parsing tests
# ---------------------------------------------------------------------------


def _profiler_json(**overrides):
    data = {
        "analysis": "Kernel analysis here.",
        "bottlenecks": "Top bottleneck: attention at 45%.",
        "suggestions": "Use CUDA graphs.",
    }
    data.update(overrides)
    return json.dumps(data)


def test_parse_profiler_response_raw_json():
    text = _profiler_json()
    resp = _parse_profiler_response_text(text)
    assert resp is not None
    assert resp.analysis == "Kernel analysis here."


def test_parse_profiler_response_fenced_json():
    text = f"```json\n{_profiler_json()}\n```"
    resp = _parse_profiler_response_text(text)
    assert resp is not None
    assert "attention" in resp.bottlenecks


def test_parse_profiler_response_with_surrounding_text():
    text = f"Here is the analysis:\n{_profiler_json()}\nDone."
    resp = _parse_profiler_response_text(text)
    assert resp is not None


def test_parse_profiler_response_empty():
    assert _parse_profiler_response_text("") is None
    assert _parse_profiler_response_text("no json here") is None


def test_parse_profiler_response_invalid_json():
    assert _parse_profiler_response_text("{invalid json}") is None


# ---------------------------------------------------------------------------
# run_profiler_agent tests
# ---------------------------------------------------------------------------


def test_run_profiler_agent_structured_response():
    """Agent returns structured response via stream."""
    agent = MagicMock()
    resp_data = ProfilerResponse(
        analysis="Good profile data.",
        bottlenecks="Attention dominates.",
        suggestions="No action needed.",
    )
    agent.stream.return_value = iter(
        [
            {
                "agent": {
                    "messages": [MagicMock(content="Profiled.", type="ai")],
                    "structured_response": resp_data,
                }
            }
        ]
    )
    result = run_profiler_agent(agent, "Profile the server.")
    assert result.analysis == "Good profile data."
    assert result.bottlenecks == "Attention dominates."


def test_run_profiler_agent_fallback_json_parsing():
    """Agent returns JSON text instead of structured response."""
    agent = MagicMock()
    json_text = _profiler_json(analysis="Fallback parsing.")
    agent.stream.return_value = iter(
        [
            {
                "agent": {
                    "messages": [MagicMock(content=json_text, type="ai")],
                }
            }
        ]
    )
    result = run_profiler_agent(agent, "Profile the server.")
    assert result.analysis == "Fallback parsing."


def test_run_profiler_agent_no_response():
    """Agent returns no parseable response."""
    agent = MagicMock()
    agent.stream.return_value = iter(
        [
            {
                "agent": {
                    "messages": [MagicMock(content="I couldn't profile.", type="ai")],
                }
            }
        ]
    )
    result = run_profiler_agent(agent, "Profile the server.")
    assert "No structured response" in result.analysis
