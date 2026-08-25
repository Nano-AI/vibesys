"""Focused tests for the Omnigent agent-driver adapter."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import json
import os
import shlex
import shutil
import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from vibesys.agents.contracts import (
    AgentEvent,
    AgentExecutionPolicy,
    AgentSessionSpec,
    AgentTurnRequest,
    MCPServerSpec,
)
from vibesys.agents.drivers import omnigent as driver_subject
from vibesys.agents.drivers.omnigent import (
    _TOOL_EXECUTOR_ATTR,
    OmnigentDriver,
    OmnigentDriverError,
    OmnigentSession,
    _build_os_tools,
    _leased_session_environment,
    _patched_environ,
)
from vibesys.agents.omnigent.providers import OMNIGENT_PROVIDER_EXECUTORS
from vibesys.schemas import JudgeResponse
from vs_sandbox import HostResource, ProjectPathPolicy

omnigent = pytest.importorskip("omnigent")
TextChunk = omnigent.TextChunk
ToolCallComplete = omnigent.ToolCallComplete
ToolCallRequest = omnigent.ToolCallRequest
TurnComplete = omnigent.TurnComplete


def _sandbox_backend_available() -> bool:
    if os.environ.get("VIBESYS_REQUIRE_SANDBOX_TESTS") == "1":
        return True
    if sys.platform.startswith("linux"):
        return shutil.which("bwrap") is not None
    if sys.platform == "darwin":
        return shutil.which("sandbox-exec") is not None
    return False


requires_sandbox_backend = pytest.mark.skipif(
    not _sandbox_backend_available(),
    reason="requires the platform sandbox backend (bwrap / sandbox-exec)",
)


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_registered_executor_matches_pinned_omnigent_api(provider: str) -> None:
    driver = OmnigentDriver()
    executor_spec = OMNIGENT_PROVIDER_EXECUTORS[provider]

    executor_class = driver._executor_class(executor_spec)  # noqa: SLF001
    parameters = inspect.signature(executor_class.__init__).parameters

    assert executor_class.__name__ == executor_spec.class_name
    assert "cwd" in parameters
    assert "model" in parameters


@pytest.mark.parametrize(
    ("provider", "required_binary"),
    [("claude", None), ("codex", "codex")],
)
def test_registered_executor_exposes_tool_dispatch_seam(
    provider: str,
    required_binary: str | None,
) -> None:
    if required_binary is not None and shutil.which(required_binary) is None:
        pytest.skip(f"{provider} executor needs the {required_binary!r} CLI to construct")
    driver = OmnigentDriver()
    executor_class = driver._executor_class(  # noqa: SLF001
        OMNIGENT_PROVIDER_EXECUTORS[provider]
    )

    executor = executor_class(cwd=".", model=None)
    try:
        assert hasattr(executor, _TOOL_EXECUTOR_ATTR)
    finally:
        driver.close_executor(executor)


class _FakeExecutor:
    def __init__(self, events: list[Any], *, delay: float = 0) -> None:
        self.events = events
        self.delay = delay
        self.calls: list[tuple[Any, ...]] = []
        self.close_calls = 0
        self.thread_id = "provider-session"
        self._tool_executor = None

    def run_turn(self, messages, tools, instructions, config=None):  # noqa: ANN001, ANN202
        self.calls.append((messages, tools, instructions, config, os.environ.get("DRIVER_TEST")))

        async def stream():  # noqa: ANN202
            if self.delay:
                await asyncio.sleep(self.delay)
            for event in self.events:
                yield event

        return stream()

    async def close(self) -> None:
        self.close_calls += 1


@dataclass
class _Observer:
    events: list[AgentEvent] = field(default_factory=list)

    def on_event(self, event: AgentEvent) -> None:
        self.events.append(event)


def _spec(tmp_path: Path, **changes: Any) -> AgentSessionSpec:  # noqa: ANN401
    base = AgentSessionSpec(
        role="judge",
        provider="codex",
        workspace=tmp_path,
        model="gpt-5.5",
        policy=AgentExecutionPolicy(),
    )
    return replace(base, **changes)


def _session(tmp_path: Path, executor: _FakeExecutor) -> tuple[OmnigentDriver, OmnigentSession]:
    driver = OmnigentDriver()
    session = OmnigentSession(
        driver=driver,
        spec=_spec(tmp_path, reasoning_effort="high", environment=(("DRIVER_TEST", "set"),)),
        executor=executor,
        tool_schemas=[{"name": "sys_os_read"}],
    )
    driver._sessions.add(session)  # noqa: SLF001
    return driver, session


def test_identical_environment_overrides_restore_after_the_final_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "VIBESYS_TEST_CONCURRENT_ENV"
    monkeypatch.delenv(key, raising=False)
    first_entered = threading.Event()
    second_entered = threading.Event()
    first_exited = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()

    def first() -> None:
        with _patched_environ({key: "shared"}):
            first_entered.set()
            assert second_entered.wait(timeout=2)
            assert release_first.wait(timeout=2)
        first_exited.set()

    def second() -> None:
        assert first_entered.wait(timeout=2)
        with _patched_environ({key: "shared"}):
            second_entered.set()
            assert first_exited.wait(timeout=2)
            assert os.environ[key] == "shared"
            assert release_second.wait(timeout=2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        second_future = pool.submit(second)
        assert second_entered.wait(timeout=2)
        release_first.set()
        assert first_exited.wait(timeout=2)
        release_second.set()
        first_future.result(timeout=2)
        second_future.result(timeout=2)

    assert key not in os.environ


def test_conflicting_environment_overrides_fail_without_corrupting_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "VIBESYS_TEST_CONFLICTING_ENV"
    monkeypatch.delenv(key, raising=False)
    entered = threading.Event()
    release = threading.Event()

    def owner() -> None:
        with _patched_environ({key: "optimizer"}):
            entered.set()
            assert release.wait(timeout=2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(owner)
        assert entered.wait(timeout=2)
        try:
            with (
                pytest.raises(OmnigentDriverError, match=key),
                _patched_environ({key: "chat"}),
            ):
                pytest.fail("conflicting environment context must not start")
            assert os.environ[key] == "optimizer"
        finally:
            release.set()
        future.result(timeout=2)

    assert key not in os.environ


def test_omitted_environment_override_cannot_inherit_an_active_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "VIBESYS_TEST_OMITTED_ENV"
    monkeypatch.delenv(key, raising=False)
    entered = threading.Event()
    release = threading.Event()

    def owner() -> None:
        with _leased_session_environment({key: "optimizer"}):
            entered.set()
            assert release.wait(timeout=2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(owner)
        assert entered.wait(timeout=2)
        try:
            with (
                pytest.raises(OmnigentDriverError, match=key),
                _leased_session_environment({}),
            ):
                pytest.fail("an omitted environment value must not be inherited")
        finally:
            release.set()
        future.result(timeout=2)

    assert key not in os.environ


def test_identical_session_environments_allow_nested_tool_superset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_key = "VIBESYS_TEST_SESSION_ENV"
    tool_key = "VIBESYS_TEST_RUST_TOOL_ENV"
    monkeypatch.delenv(session_key, raising=False)
    monkeypatch.delenv(tool_key, raising=False)
    both_entered = threading.Barrier(2)
    tool_entered = threading.Event()
    release_tool = threading.Event()

    def optimizer() -> None:
        environment = {session_key: "shared"}
        with _leased_session_environment(environment), _patched_environ(environment):
            both_entered.wait(timeout=2)
            with _patched_environ({**environment, tool_key: "scratch/cargo-home"}):
                tool_entered.set()
                assert release_tool.wait(timeout=2)

    def chat() -> None:
        environment = {session_key: "shared"}
        with _leased_session_environment(environment), _patched_environ(environment):
            both_entered.wait(timeout=2)
            assert tool_entered.wait(timeout=2)
            assert os.environ[session_key] == "shared"
            release_tool.set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(optimizer), pool.submit(chat)]
        for future in futures:
            future.result(timeout=2)

    assert session_key not in os.environ
    assert tool_key not in os.environ


def test_turn_allows_rust_tool_environment_superset_in_worker_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rust_key = "VIBESYS_TEST_RUST_TOOL_PATH"
    monkeypatch.delenv(rust_key, raising=False)

    class RustToolExecutor(_FakeExecutor):
        def run_turn(self, *_args: object, **_kwargs: object):  # noqa: ANN202
            async def stream():  # noqa: ANN202
                def invoke_tool() -> None:
                    with _patched_environ(
                        {
                            "DRIVER_TEST": "set",
                            rust_key: "scratch/cargo-home",
                        }
                    ):
                        assert os.environ["DRIVER_TEST"] == "set"
                        assert os.environ[rust_key] == "scratch/cargo-home"

                await asyncio.to_thread(invoke_tool)
                yield TurnComplete(response="tool complete")

            return stream()

    driver, session = _session(tmp_path, RustToolExecutor([]))

    assert session.run_turn(AgentTurnRequest("use cargo")).text == "tool complete"

    driver.close()
    assert rust_key not in os.environ


def test_distinct_tool_dispatches_scope_rust_environment_without_process_patch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent.inner import os_env as omnigent_os_env  # noqa: PLC0415
    from omnigent.tools.builtins import os_env as omnigent_os_tools  # noqa: PLC0415

    key = "CARGO_HOME"
    monkeypatch.setenv(key, "ambient")
    invoked = threading.Barrier(2)

    class Resource:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    class ShellTool:
        @staticmethod
        def name() -> str:
            return "sys_os_shell"

        @staticmethod
        def get_schema() -> dict[str, Any]:
            return {
                "function": {
                    "name": "sys_os_shell",
                    "description": "shell",
                    "parameters": {"type": "object"},
                }
            }

        def invoke(self, arguments: str, _context: Any) -> str:  # noqa: ANN401
            invoked.wait(timeout=2)
            return str(json.loads(arguments)["command"])

    resources: list[Resource] = []

    def create_environment(_spec: Any) -> Resource:  # noqa: ANN401
        resource = Resource()
        resources.append(resource)
        return resource

    monkeypatch.setattr(omnigent_os_env, "create_os_environment", create_environment)
    monkeypatch.setattr(omnigent_os_tools, "build_os_env_tools", lambda _env: [ShellTool()])
    cargo_homes = [str(tmp_path / "optimizer cargo"), str(tmp_path / "chat cargo")]
    built = [_build_os_tools(object(), tmp_path, {key: cargo_home}) for cargo_home in cargo_homes]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(asyncio.run, dispatch("sys_os_shell", {"command": "cargo test"}))
            for _, dispatch, _ in built
        ]
        commands = [future.result(timeout=2) for future in futures]

    assert commands == [
        f"export CARGO_HOME={shlex.quote(cargo_homes[0])}; cargo test",
        f"export CARGO_HOME={shlex.quote(cargo_homes[1])}; cargo test",
    ]
    assert os.environ[key] == "ambient"
    for _, _, os_environments in built:
        for os_environment in os_environments:
            os_environment.close()
    assert [resource.close_calls for resource in resources] == [1, 1]


def test_turn_normalizes_events_usage_schema_and_session_id(tmp_path: Path) -> None:
    executor = _FakeExecutor(
        [
            TextChunk("partial"),
            ToolCallRequest(name="Read", args={"path": "a"}),
            ToolCallComplete(name="Read", result="contents"),
            TurnComplete(response="final", usage={"input_tokens": 3, "output_tokens": 5}),
        ]
    )
    driver, session = _session(tmp_path, executor)
    observer = _Observer()

    result = session.run_turn(
        AgentTurnRequest(
            "answer",
            instructions="system",
            output_schema=JudgeResponse,
        ),
        observer,
    )

    assert result.text == "final"
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 5
    assert result.provider_session_id == "provider-session"
    messages, tools, instructions, config, environment = executor.calls[0]
    assert messages[0]["content"].startswith("answer")
    assert "JudgeResponse" in messages[0]["content"]
    assert tools == [{"name": "sys_os_read"}]
    assert instructions == "system"
    assert config.extra["reasoning_effort"] == "high"
    assert environment == "set"
    assert "DRIVER_TEST" not in os.environ
    assert [event.kind.value for event in observer.events] == [
        "text",
        "tool_call",
        "tool_result",
        "usage",
    ]
    driver.close()


def test_turn_complete_response_wins_and_chunks_are_fallback(tmp_path: Path) -> None:
    final_executor = _FakeExecutor([TextChunk("chunk"), TurnComplete(response="final")])
    final_driver, final_session = _session(tmp_path, final_executor)
    assert final_session.run_turn(AgentTurnRequest("one")).text == "final"
    final_driver.close()

    chunk_executor = _FakeExecutor([TextChunk("one "), TextChunk("two")])
    chunk_driver, chunk_session = _session(tmp_path, chunk_executor)
    assert chunk_session.run_turn(AgentTurnRequest("two")).text == "one two"
    chunk_driver.close()


def test_timeout_poisons_session_and_cleanup_is_idempotent(tmp_path: Path) -> None:
    executor = _FakeExecutor([], delay=0.1)
    driver, session = _session(tmp_path, executor)

    with pytest.raises(TimeoutError):
        session.run_turn(AgentTurnRequest("slow", timeout=timedelta(milliseconds=1)))
    with pytest.raises(RuntimeError, match="must be reset"):
        session.run_turn(AgentTurnRequest("again"))

    session.close()
    session.close()
    driver.close()
    assert executor.close_calls == 1


def test_session_cleanup_closes_owned_os_environments(tmp_path: Path) -> None:
    class Resource:
        close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    executor = _FakeExecutor([])
    resource = Resource()
    driver, _session_instance = _session(tmp_path, executor)
    driver._executor_os_environments[id(executor)] = (resource,)  # noqa: SLF001

    driver.close()

    assert resource.close_calls == 1


def test_close_cancels_an_active_turn_from_another_thread(tmp_path: Path) -> None:
    started = threading.Event()
    finalized = threading.Event()

    class NeverEndingExecutor(_FakeExecutor):
        def run_turn(self, *_args: object, **_kwargs: object):  # noqa: ANN202
            async def stream():  # noqa: ANN202
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    finalized.set()
                if False:
                    yield None

            return stream()

    executor = NeverEndingExecutor([])
    driver, session = _session(tmp_path, executor)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        turn = pool.submit(session.run_turn, AgentTurnRequest("work forever"))
        assert started.wait(timeout=2)
        session.close()
        with pytest.raises(concurrent.futures.CancelledError):
            turn.result(timeout=2)

    assert finalized.is_set()
    driver.close()
    assert executor.close_calls == 1


def test_close_drains_blocking_default_executor_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(driver_subject, "_SESSION_SHUTDOWN_TIMEOUT", 0.01)
    worker_started = threading.Event()
    release_worker = threading.Event()
    close_finished = threading.Event()

    class ThreadedExecutor(_FakeExecutor):
        def run_turn(self, *_args: object, **_kwargs: object):  # noqa: ANN202
            async def stream():  # noqa: ANN202
                def block() -> None:
                    worker_started.set()
                    release_worker.wait(timeout=2)

                await asyncio.to_thread(block)
                if False:
                    yield None

            return stream()

    executor = ThreadedExecutor([])
    driver, session = _session(tmp_path, executor)
    turn_thread = threading.Thread(target=lambda: _ignore_cancelled_turn(session))
    turn_thread.start()
    assert worker_started.wait(timeout=2)
    close_thread = threading.Thread(target=lambda: (session.close(), close_finished.set()))
    close_thread.start()

    assert not close_finished.wait(timeout=0.05)
    assert session in driver._sessions  # noqa: SLF001
    release_worker.set()
    close_thread.join(timeout=2)
    turn_thread.join(timeout=2)

    assert close_finished.is_set()
    assert not close_thread.is_alive()
    assert not turn_thread.is_alive()
    driver.close()
    assert executor.close_calls == 1


@pytest.mark.parametrize("failure", ["start", "ready"])
def test_session_setup_failure_closes_executor_scratch_and_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(driver_subject, "_SESSION_SHUTDOWN_TIMEOUT", 0.01)
    executor = _FakeExecutor([])
    driver = OmnigentDriver()
    scratch_cleanups = 0

    class Scratch:
        def cleanup(self) -> None:
            nonlocal scratch_cleanups
            scratch_cleanups += 1

    def build(_spec: AgentSessionSpec) -> tuple[_FakeExecutor, list[dict[str, Any]]]:
        scratch: Any = Scratch()
        driver._executor_scratch[id(executor)] = scratch  # noqa: SLF001
        return executor, []

    monkeypatch.setattr(driver, "_build_executor", build)
    created_loops: list[asyncio.AbstractEventLoop] = []
    new_event_loop = asyncio.new_event_loop

    def capture_loop() -> asyncio.AbstractEventLoop:
        loop = new_event_loop()
        created_loops.append(loop)
        return loop

    monkeypatch.setattr(driver_subject.asyncio, "new_event_loop", capture_loop)
    if failure == "start":

        def fail_start(_thread: threading.Thread) -> None:
            raise RuntimeError("thread start failed")  # noqa: TRY003  # test sentinel

        monkeypatch.setattr(driver_subject.threading.Thread, "start", fail_start)
        error = "thread start failed"
    else:
        monkeypatch.setattr(OmnigentSession, "_serve_loop", lambda _self: None)
        error = "event loop did not start"

    with pytest.raises(RuntimeError, match=error):
        driver.create_session(_spec(tmp_path))

    assert executor.close_calls == 1
    assert scratch_cleanups == 1
    assert len(created_loops) == 1
    assert created_loops[0].is_closed()
    assert driver._sessions == set()  # noqa: SLF001
    driver.close()


def _ignore_cancelled_turn(session: OmnigentSession) -> None:
    with contextlib.suppress(concurrent.futures.CancelledError):
        session.run_turn(AgentTurnRequest("use a worker"))


def test_concurrent_session_close_callers_receive_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, session = _session(tmp_path, _FakeExecutor([]))
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    def fail_cleanup(_active: concurrent.futures.Future[Any] | None) -> None:
        cleanup_started.set()
        assert release_cleanup.wait(timeout=2)
        raise KeyboardInterrupt

    monkeypatch.setattr(session, "_close_resources", fail_cleanup)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(session.close)
        assert cleanup_started.wait(timeout=2)
        waiter = pool.submit(session.close)
        release_cleanup.set()
        for future in (owner, waiter):
            with pytest.raises(KeyboardInterrupt):
                future.result(timeout=2)

    assert session._close_finished.is_set()  # noqa: SLF001
    driver._sessions.clear()  # noqa: SLF001
    driver.close()


def test_independent_sessions_overlap_and_clean_up_their_own_loops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)
    active_lock = threading.Lock()
    active = 0
    maximum_active = 0

    class OverlappingExecutor(_FakeExecutor):
        def __init__(self, answer: str) -> None:
            super().__init__([])
            self.answer = answer

        def run_turn(self, *_args: object, **_kwargs: object):  # noqa: ANN202
            async def stream():  # noqa: ANN202
                nonlocal active, maximum_active
                with active_lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                try:
                    barrier.wait(timeout=2)
                    await asyncio.sleep(0)
                    yield TurnComplete(response=self.answer)
                finally:
                    with active_lock:
                        active -= 1

            return stream()

    driver = OmnigentDriver()
    executors = [OverlappingExecutor("optimizer"), OverlappingExecutor("chat")]
    remaining = iter(executors)
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (next(remaining), []))
    sessions = [driver.create_session(_spec(tmp_path)) for _ in executors]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(session.run_turn, AgentTurnRequest("work")) for session in sessions]
        results = [future.result(timeout=3) for future in futures]

    assert [result.text for result in results] == ["optimizer", "chat"]
    assert maximum_active == 2
    assert active == 0

    driver.close()
    assert [executor.close_calls for executor in executors] == [1, 1]
    assert all(session._loop.is_closed() for session in sessions)  # noqa: SLF001
    assert driver._sessions == set()  # noqa: SLF001


def test_driver_close_continues_after_cleanup_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingCloseExecutor(_FakeExecutor):
        async def close(self) -> None:
            self.close_calls += 1
            raise KeyboardInterrupt

    driver = OmnigentDriver()
    executors = [FailingCloseExecutor([]), _FakeExecutor([])]
    remaining = iter(executors)
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (next(remaining), []))
    sessions = [driver.create_session(_spec(tmp_path)) for _ in executors]

    with pytest.raises(KeyboardInterrupt):
        driver.close()

    assert [executor.close_calls for executor in executors] == [1, 1]
    assert all(session._loop.is_closed() for session in sessions)  # noqa: SLF001
    assert driver._sessions == set()  # noqa: SLF001


def test_concurrent_driver_close_callers_receive_base_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _FakeExecutor([])
    driver, session = _session(tmp_path, executor)
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()
    original_close = session.close

    def fail_close() -> None:
        cleanup_started.set()
        assert release_cleanup.wait(timeout=2)
        raise KeyboardInterrupt

    monkeypatch.setattr(session, "close", fail_close)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(driver.close)
        assert cleanup_started.wait(timeout=2)
        waiter = pool.submit(driver.close)
        release_cleanup.set()
        for future in (owner, waiter):
            with pytest.raises(KeyboardInterrupt):
                future.result(timeout=2)

    assert driver._close_finished.is_set()  # noqa: SLF001
    original_close()
    assert executor.close_calls == 1


def test_driver_close_waits_for_in_flight_session_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_started = threading.Event()
    release_build = threading.Event()
    close_finished = [threading.Event(), threading.Event()]
    executor = _FakeExecutor([])
    driver = OmnigentDriver()

    def build(_spec: AgentSessionSpec) -> tuple[_FakeExecutor, list[dict[str, Any]]]:
        build_started.set()
        assert release_build.wait(timeout=2)
        return executor, []

    monkeypatch.setattr(driver, "_build_executor", build)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        creation = pool.submit(driver.create_session, _spec(tmp_path))
        assert build_started.wait(timeout=2)
        closing = pool.submit(lambda: (driver.close(), close_finished[0].set()))
        while True:
            with driver._lifecycle:  # noqa: SLF001
                if driver._closed:  # noqa: SLF001
                    break
        second_closing = pool.submit(lambda: (driver.close(), close_finished[1].set()))
        assert not close_finished[0].wait(timeout=0.05)
        assert not close_finished[1].wait(timeout=0.05)
        release_build.set()
        with pytest.raises(RuntimeError, match="closed"):
            creation.result(timeout=2)
        closing.result(timeout=2)
        second_closing.result(timeout=2)

    assert all(finished.is_set() for finished in close_finished)
    assert executor.close_calls == 1
    assert driver._sessions == set()  # noqa: SLF001


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"mcp_servers": (MCPServerSpec("issues", "python"),)}, "MCP"),
        (
            {
                "policy": AgentExecutionPolicy(
                    host_resources=(HostResource(Path("model")),),
                )
            },
            "host-resource",
        ),
        (
            {
                "policy": AgentExecutionPolicy(
                    project_paths=ProjectPathPolicy(read_only_paths=("src/protected",)),
                )
            },
            "top-level",
        ),
        ({"policy": AgentExecutionPolicy(containerized=True)}, "container"),
    ],
)
def test_create_session_rejects_unsupported_requirements_before_building(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    message: str,
) -> None:
    driver = OmnigentDriver()

    def build(_spec: AgentSessionSpec) -> tuple[Any, list[dict[str, Any]]]:
        pytest.fail("executor must not be built")

    monkeypatch.setattr(driver, "_build_executor", build)

    with pytest.raises(OmnigentDriverError, match=message):
        driver.create_session(_spec(tmp_path, **change))


def test_create_session_accepts_supported_top_level_project_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = OmnigentDriver()
    executor = _FakeExecutor([])
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (executor, []))
    policy = ProjectPathPolicy(
        read_only_paths=(".git", ".vibesys"),
        hidden_paths=(".env",),
    )

    session = driver.create_session(
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy))
    )

    session.close()
    driver.close()


def test_create_session_accepts_non_dot_hidden_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = OmnigentDriver()
    executor = _FakeExecutor([])
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (executor, []))
    policy = ProjectPathPolicy(hidden_paths=("agent.toml",))

    session = driver.create_session(
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy))
    )

    session.close()
    driver.close()


def test_driver_owns_session_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = _FakeExecutor([])
    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_build_executor", lambda _spec: (executor, []))

    driver.create_session(_spec(tmp_path))
    driver.close()
    driver.close()

    assert executor.close_calls == 1


def test_missing_private_tool_executor_seam_fails_during_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExecutorWithoutSeam:
        close_calls = 0

        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            self.close_calls += 1

    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_executor_class", lambda _spec: ExecutorWithoutSeam)
    monkeypatch.setattr(driver, "_build_os_env", lambda _workspace, **_kwargs: object())

    with pytest.raises(OmnigentDriverError, match="_tool_executor"):
        driver._build_executor(_spec(tmp_path))  # noqa: SLF001


def test_os_policy_is_always_sandboxed_and_workspace_scoped(tmp_path: Path) -> None:
    driver = OmnigentDriver()

    spec = driver._build_os_env(_spec(tmp_path))  # noqa: SLF001

    assert spec.sandbox is not None
    assert spec.sandbox.type != "none"
    assert spec.sandbox.read_paths is None
    assert spec.sandbox.write_paths == [str(tmp_path)]
    assert spec.cwd == str(tmp_path)


def test_os_policy_exposes_control_dotdirs_and_keeps_hidden_dotfiles_masked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".vibesys").mkdir()
    (tmp_path / ".codex-tmp").mkdir()
    (tmp_path / ".env").write_text("secret", encoding="utf-8")
    (tmp_path / "agent.toml").write_text("secret", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "secret").write_text("secret", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "Cargo.toml").write_text("[package]", encoding="utf-8")
    toolchain = tmp_path.parent / "toolchain"
    toolchain.mkdir(exist_ok=True)
    policy = ProjectPathPolicy(
        read_only_paths=(".git", ".vibesys"),
        hidden_paths=(".env", "agent.toml", "config/secret"),
    )
    driver = OmnigentDriver()
    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent.declare_active_rust_toolchain_resources",
        lambda *_args, **_kwargs: (HostResource(toolchain, purpose="Rust toolchain"),),
    )

    spec = driver._build_os_env(  # noqa: SLF001
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy)),
        env_passthrough=("CARGO_HOME",),
        include_toolchain=True,
    )

    assert spec.sandbox is not None
    assert spec.sandbox.write_paths == [str(tmp_path)]
    assert spec.sandbox.write_files is None
    assert spec.sandbox.read_paths == [str(toolchain)]
    assert spec.sandbox.env_passthrough == ["CARGO_HOME"]
    assert set(spec.sandbox.cwd_allow_hidden or ()) == {".git", ".vibesys"}
    assert spec.sandbox.cwd_hidden_scan_recursive is False
    assert spec.sandbox.cwd_hidden_scan_overflow == "error"
    assert spec.sandbox.mask_paths == [
        ".codex-tmp",
        ".env",
        "agent.toml",
        "config/secret",
    ]


@requires_sandbox_backend
def test_os_policy_masks_declared_non_dot_and_nested_paths(tmp_path: Path) -> None:
    (tmp_path / "public.txt").write_text("public-4417", encoding="utf-8")
    (tmp_path / "agent.toml").write_text("secret-9913", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "secret").write_text("secret-7729", encoding="utf-8")
    policy = ProjectPathPolicy(hidden_paths=("agent.toml", "config/secret"))
    driver = OmnigentDriver()
    spec = driver._build_os_env(  # noqa: SLF001
        _spec(tmp_path, policy=AgentExecutionPolicy(project_paths=policy))
    )
    _, dispatch, os_environments = _build_os_tools(spec, tmp_path)

    try:
        public = asyncio.run(dispatch("sys_os_read", {"path": "public.txt"}))
        hidden = asyncio.run(dispatch("sys_os_read", {"path": "agent.toml"}))
        nested = asyncio.run(dispatch("sys_os_read", {"path": "config/secret"}))
    finally:
        for os_environment in os_environments:
            os_environment.close()

    assert "public-4417" in str(public)
    assert "secret-9913" not in str(hidden)
    assert "error" in str(hidden).lower()
    assert "secret-7729" not in str(nested)
    assert "error" in str(nested).lower()


def test_codex_executor_disables_native_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Executor(_FakeExecutor):
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            captured.update(kwargs)
            super().__init__([])

    driver = OmnigentDriver()
    monkeypatch.setattr(driver, "_executor_class", lambda _spec: Executor)
    monkeypatch.setattr(driver, "_build_os_env", lambda _spec, **_kwargs: object())
    rust_sysroot = tmp_path / "rust"
    target_libdir = rust_sysroot / "lib" / "rustlib" / "x86_64-unknown-linux-gnu" / "lib"
    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent.resolve_active_rust_toolchain",
        lambda _context, *, workspace: (rust_sysroot, target_libdir),  # noqa: ARG005
    )

    def build_tools(
        _os_env: object,
        _workspace: Path,
        environment: dict[str, str],
        _shell_os_env: object,
    ) -> tuple[list[dict[str, Any]], object, tuple[Any, ...]]:
        captured["tool_environment"] = environment
        return [], lambda _name, _args: None, ()

    monkeypatch.setattr(
        "vibesys.agents.drivers.omnigent._build_os_tools",
        build_tools,
    )

    executor, _schemas = driver._build_executor(_spec(tmp_path))  # noqa: SLF001

    assert captured["disable_native_tools"] is True
    assert str(captured["tool_environment"]["PATH"]).split(os.pathsep)[0] == str(
        rust_sysroot / "bin"
    )
    cargo_home = Path(captured["tool_environment"]["CARGO_HOME"])
    assert cargo_home.name == "cargo-home"
    assert cargo_home.parent.is_dir()
    assert not cargo_home.is_relative_to(tmp_path)
    assert "CARGO_TARGET_DIR" not in captured["tool_environment"]
    assert not any(key.endswith("_LINKER") for key in captured["tool_environment"])
    driver.close_executor(executor)
    assert not cargo_home.parent.exists()
