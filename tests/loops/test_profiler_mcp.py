"""Tests for the profiler MCP servers (torch).

We verify tool registration via ``FastMCP.list_tools`` and exercise a few
tools end-to-end through ``FastMCP.call_tool``. The stdio JSON-RPC framing
itself is the ``mcp`` package's responsibility.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


# The servers live under examples/ (co-located with the analysis scripts) so
# importing them by file path keeps the tests decoupled from sys.path state.
def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    # Inject the server's parent dir onto sys.path BEFORE exec so the
    # server's ``import analyze_torch_profile`` succeeds.
    parent = str(path.parent)
    inserted = False
    if parent not in sys.path:
        sys.path.insert(0, parent)
        inserted = True
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(parent)
    return module


_REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def torch_server_mod():
    return _load_module(
        "_torch_server",
        _REPO / "examples" / "torch_profiler" / "server.py",
    )


async def _list_tool_names(server) -> set[str]:
    tools = await server.list_tools()
    return {t.name for t in tools}


async def _call_tool(server, name: str, **kwargs) -> str:
    _, structured = await server.call_tool(name, kwargs)
    return structured["result"]


# ---------------------------------------------------------------------------
# torch MCP server
# ---------------------------------------------------------------------------


class TestTorchMcpServer:
    def test_registers_expected_tools(self, torch_server_mod):
        server = torch_server_mod.build_server()
        names = asyncio.run(_list_tool_names(server))
        assert names == {
            "tables",
            "kernels",
            "operators",
            "cpu_overhead",
            "memory",
            "summary",
        }

    def test_tables_tool_reports_prof_json_overview(self, torch_server_mod, tmp_path):
        prof = tmp_path / "prof.json"
        prof.write_text(
            json.dumps(
                {
                    "version": 1,
                    "captured_at": "2026-04-22T00:00:00Z",
                    "mode": "model",
                    "total_cuda_time_us": 1234.5,
                    "total_cpu_time_us": 567.8,
                    "events": [
                        {
                            "name": "aten::mm",
                            "category": "operator",
                            "cpu_time_us": 100,
                            "cuda_time_us": 200,
                            "self_cpu_time_us": 50,
                            "self_cuda_time_us": 200,
                            "count": 3,
                        },
                        {
                            "name": "flash_fwd_kernel",
                            "category": "kernel",
                            "cpu_time_us": 10,
                            "cuda_time_us": 500,
                            "self_cpu_time_us": 10,
                            "self_cuda_time_us": 500,
                            "count": 2,
                        },
                    ],
                }
            )
        )

        server = torch_server_mod.build_server()
        out = asyncio.run(_call_tool(server, "tables", report=str(prof)))
        assert "2026-04-22" in out
        assert "kernel" in out
        assert "operator" in out

    def test_kernels_tool_ranks_by_self_cuda(self, torch_server_mod, tmp_path):
        prof = tmp_path / "prof.json"
        prof.write_text(
            json.dumps(
                {
                    "version": 1,
                    "total_cuda_time_us": 700.0,
                    "total_cpu_time_us": 110.0,
                    "events": [
                        {
                            "name": "flash_fwd_kernel",
                            "category": "kernel",
                            "cpu_time_us": 10,
                            "cuda_time_us": 500,
                            "self_cpu_time_us": 10,
                            "self_cuda_time_us": 500,
                            "count": 2,
                        },
                        {
                            "name": "rms_norm_kernel",
                            "category": "kernel",
                            "cpu_time_us": 5,
                            "cuda_time_us": 200,
                            "self_cpu_time_us": 5,
                            "self_cuda_time_us": 200,
                            "count": 8,
                        },
                    ],
                }
            )
        )

        server = torch_server_mod.build_server()
        out = asyncio.run(_call_tool(server, "kernels", report=str(prof), top=5))
        # flash_fwd_kernel is the bigger one and should appear first.
        assert out.index("flash_fwd_kernel") < out.index("rms_norm_kernel")
