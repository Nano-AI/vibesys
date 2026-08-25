"""Omnigent implementation of the stateful agent-driver contract."""

# ruff: noqa: TRY003

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import os
import re
import shlex
import sys
import tempfile
import threading
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibesys.agents.cli_common import build_schema_hint
from vibesys.agents.contracts import (
    AgentCapabilities,
    AgentEvent,
    AgentEventKind,
    AgentObserver,
    AgentSessionSpec,
    AgentTurnRequest,
    AgentTurnResult,
    AgentUsage,
)
from vibesys.agents.host_resource_declarations import (
    declare_active_rust_toolchain_resources,
    resolve_active_rust_toolchain,
)
from vibesys.agents.omnigent.providers import (
    OMNIGENT_PROVIDER_EXECUTORS,
    OmnigentExecutorSpec,
    supported_providers,
)
from vs_sandbox import HostResourceContext

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

_TOOL_EXECUTOR_ATTR = "_tool_executor"
"""Private Omnigent 0.10.0 tool-dispatch seam, guarded before assignment."""

_OMNIGENT_INTERNAL_HIDDEN = frozenset({".codex-tmp"})
"""Runtime-owned workspace paths that OS tools must not traverse."""

_ENVIRONMENT_MISSING = object()
_ENVIRONMENT_OVERRIDE_LOCK = threading.Lock()
_ENVIRONMENT_OVERRIDES: dict[str, tuple[object, str, int]] = {}
"""Reference-counted process-environment values used by subprocesses."""

_SESSION_ENVIRONMENT_LOCK = threading.Lock()
_session_environment: tuple[dict[str, str], int] | None = None
"""Complete environment-map lease shared by concurrent top-level turns."""

_SESSION_SHUTDOWN_TIMEOUT = 5.0
_SHELL_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _unwrap_turn(
    outcome: tuple[AgentTurnResult | None, BaseException | None],
) -> AgentTurnResult:
    result, error = outcome
    if error is not None:
        raise error
    assert result is not None  # noqa: S101  # paired result/error contract
    return result


class OmnigentDriverError(RuntimeError):
    """An Omnigent driver requirement could not be satisfied safely."""


def _is_top_level_dot_path(path: Path) -> bool:
    return len(path.parts) == 1 and path.name.startswith(".")


def _resolve_executor_spec(provider: str) -> OmnigentExecutorSpec:
    spec = OMNIGENT_PROVIDER_EXECUTORS.get(provider)
    if spec is None:
        raise OmnigentDriverError(
            f"Omnigent does not support agent provider {provider!r}; "
            f"supported providers: {supported_providers()}"
        )
    return spec


def _missing_omnigent(what: str, exc: ImportError) -> OmnigentDriverError:
    return OmnigentDriverError(
        f"{what} is not importable ({type(exc).__name__}: {exc}). "
        "Install the Omnigent optional dependency with `uv sync --extra omnigent`."
    )


def _sandbox_backend_for_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux_bwrap"
    if sys.platform == "darwin":
        return "darwin_seatbelt"
    if os.name == "nt":
        return "windows_jobobject"
    raise OmnigentDriverError(f"Omnigent has no sandbox backend for platform {sys.platform!r}")


@contextlib.contextmanager
def _patched_environ(overrides: dict[str, str]) -> Generator[None]:
    """Expose compatible values until their final concurrent user exits."""
    with _ENVIRONMENT_OVERRIDE_LOCK:
        conflicts = sorted(
            key
            for key, value in overrides.items()
            if key in _ENVIRONMENT_OVERRIDES and _ENVIRONMENT_OVERRIDES[key][1] != value
        )
        if conflicts:
            names = ", ".join(conflicts)
            raise OmnigentDriverError(
                f"Concurrent Omnigent sessions requested conflicting environment values: {names}"
            )
        for key, value in overrides.items():
            active = _ENVIRONMENT_OVERRIDES.get(key)
            if active is None:
                previous = os.environ.get(key, _ENVIRONMENT_MISSING)
                os.environ[key] = value
                _ENVIRONMENT_OVERRIDES[key] = (previous, value, 1)
            else:
                previous, active_value, users = active
                _ENVIRONMENT_OVERRIDES[key] = (previous, active_value, users + 1)
    try:
        yield
    finally:
        with _ENVIRONMENT_OVERRIDE_LOCK:
            for key in overrides:
                previous, value, users = _ENVIRONMENT_OVERRIDES[key]
                if users > 1:
                    _ENVIRONMENT_OVERRIDES[key] = (previous, value, users - 1)
                else:
                    del _ENVIRONMENT_OVERRIDES[key]
                    if previous is _ENVIRONMENT_MISSING:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = str(previous)


@contextlib.contextmanager
def _leased_session_environment(overrides: dict[str, str]) -> Generator[None]:
    """Require concurrent turns to agree on their complete environment map."""
    global _session_environment  # noqa: PLW0603
    with _SESSION_ENVIRONMENT_LOCK:
        active = _session_environment
        if active is not None and active[0] != overrides:
            active_values = active[0]
            conflicts = sorted(
                key
                for key in active_values.keys() | overrides.keys()
                if active_values.get(key) != overrides.get(key)
            )
            names = ", ".join(conflicts)
            raise OmnigentDriverError(
                f"Concurrent Omnigent turns requested conflicting environment values: {names}"
            )
        _session_environment = (dict(overrides), 1 if active is None else active[1] + 1)
    try:
        yield
    finally:
        with _SESSION_ENVIRONMENT_LOCK:
            assert _session_environment is not None  # noqa: S101  # context ownership
            values, users = _session_environment
            _session_environment = None if users == 1 else (values, users - 1)


def _flatten_tool_schema(tool: Any) -> dict[str, Any]:  # noqa: ANN401
    function = tool.get_schema().get("function", {})
    return {
        "name": function.get("name"),
        "description": function.get("description"),
        "parameters": function.get("parameters", {"type": "object", "properties": {}}),
    }


def _shell_command_with_environment(command: str, environment: dict[str, str]) -> str:
    """Scope explicit environment values to one sandbox shell subprocess."""
    invalid = sorted(key for key in environment if _SHELL_ENVIRONMENT_NAME.fullmatch(key) is None)
    if invalid:
        raise OmnigentDriverError(f"Invalid shell environment variable names: {invalid}")
    if not environment:
        return command
    assignments = " ".join(
        f"{key}={shlex.quote(value)}" for key, value in sorted(environment.items())
    )
    return f"export {assignments}; {command}"


def _build_os_tools(  # noqa: C901  # construction cleans every partially-created helper
    os_env_spec: Any,  # noqa: ANN401
    workspace: Path,
    environment: dict[str, str] | None = None,
    shell_os_env_spec: Any | None = None,  # noqa: ANN401
) -> tuple[
    list[dict[str, Any]],
    Callable[[str, dict[str, Any]], Any],
    tuple[Any, ...],
]:
    """Build Omnigent's sandboxed filesystem tools and their dispatcher."""
    try:
        from omnigent.inner.os_env import create_os_environment  # noqa: PLC0415
        from omnigent.tools.base import ToolContext  # noqa: PLC0415
        from omnigent.tools.builtins.os_env import build_os_env_tools  # noqa: PLC0415
    except ImportError as exc:
        raise _missing_omnigent("Omnigent OS-environment tools", exc) from exc

    try:
        os_env = create_os_environment(os_env_spec)
    except OSError as exc:
        raise OmnigentDriverError(
            f"Omnigent cannot provide its {_sandbox_backend_for_platform()!r} "
            f"sandbox on this host: {exc}"
        ) from exc
    if os_env is None:
        raise OmnigentDriverError(
            f"Omnigent could not create a sandboxed OS environment for {workspace}"
        )

    resources = [os_env]
    try:
        tools = build_os_env_tools(os_env)
        if shell_os_env_spec is not None:
            shell_os_env = create_os_environment(shell_os_env_spec)
            if shell_os_env is None:
                raise OmnigentDriverError(  # noqa: TRY301
                    f"Omnigent could not create a sandboxed shell environment for {workspace}"
                )
            resources.append(shell_os_env)
            shell_tools = build_os_env_tools(shell_os_env)
            shell_tool = next((tool for tool in shell_tools if tool.name() == "sys_os_shell"), None)
            if shell_tool is None:  # pragma: no cover - guarded against Omnigent API drift
                raise OmnigentDriverError(  # noqa: TRY301
                    "Omnigent did not provide its sys_os_shell tool"
                )
            tools = [shell_tool if tool.name() == "sys_os_shell" else tool for tool in tools]
    except BaseException:
        for resource in resources:
            with contextlib.suppress(BaseException):
                resource.close()
        raise
    by_name = {tool.name(): tool for tool in tools}
    schemas = [_flatten_tool_schema(tool) for tool in tools]
    context = ToolContext(task_id="vibesys", agent_id="vibesys", workspace=workspace)

    async def dispatch(name: str, args: dict[str, Any]) -> Any:  # noqa: ANN401
        tool = by_name.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}"}

        def invoke() -> Any:  # noqa: ANN401
            effective_args = args
            if name == "sys_os_shell" and environment:
                command = args.get("command")
                if isinstance(command, str):
                    effective_args = {
                        **args,
                        "command": _shell_command_with_environment(command, environment),
                    }
            return tool.invoke(json.dumps(effective_args), context)

        return await asyncio.to_thread(invoke)

    return schemas, dispatch, tuple(resources)


def _usage_from_mapping(usage: dict[str, Any]) -> AgentUsage:
    return AgentUsage(
        input_tokens=usage.get("input_tokens"),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
        cache_read_input_tokens=usage.get("cache_read_input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_cost_usd=usage.get("total_cost_usd"),
        duration_ms=usage.get("duration_ms"),
    )


def _emit(observer: AgentObserver | None, event: AgentEvent) -> None:
    if observer is not None:
        observer.on_event(event)


async def _drive_turn(
    executor: Any,  # noqa: ANN401
    *,
    request: AgentTurnRequest,
    reasoning_effort: str | None,
    tool_schemas: list[dict[str, Any]],
    observer: AgentObserver | None,
) -> AgentTurnResult:
    """Translate one Omnigent event stream into neutral events and a result."""
    try:
        from omnigent import (  # noqa: PLC0415
            ExecutorConfig,
            TextChunk,
            ToolCallComplete,
            ToolCallRequest,
            TurnComplete,
        )
    except ImportError as exc:
        raise _missing_omnigent("Omnigent executor event types", exc) from exc

    message = request.message
    if request.output_schema is not None:
        message += build_schema_hint(request.output_schema)
    messages: list[Any] = [{"role": "user", "content": message}]
    config = (
        ExecutorConfig(extra={"reasoning_effort": reasoning_effort})
        if reasoning_effort is not None
        else None
    )
    chunks: list[str] = []
    response: str | None = None
    usage = AgentUsage()

    async for event in executor.run_turn(messages, tool_schemas, request.instructions, config):
        if isinstance(event, TextChunk):
            chunks.append(event.text)
            _emit(observer, AgentEvent(AgentEventKind.TEXT, text=event.text))
        elif isinstance(event, ToolCallRequest):
            _emit(
                observer,
                AgentEvent(
                    AgentEventKind.TOOL_CALL,
                    payload={"tool": event.name, "args": event.args},
                ),
            )
        elif isinstance(event, ToolCallComplete):
            _emit(
                observer,
                AgentEvent(
                    AgentEventKind.TOOL_RESULT,
                    payload={
                        "tool": event.name,
                        "stdout": str(event.result) if event.result is not None else "",
                        "stderr": str(event.error) if event.error is not None else "",
                        "exit_code": None,
                        "duration": event.duration_ms / 1000,
                        "status": getattr(event.status, "value", str(event.status)),
                    },
                ),
            )
        elif isinstance(event, TurnComplete):
            response = event.response
            usage = _usage_from_mapping(event.usage or {})
            if event.usage:
                _emit(observer, AgentEvent(AgentEventKind.USAGE, usage=usage))

    provider_session_id = getattr(executor, "thread_id", None)
    return AgentTurnResult(
        text=response if response is not None else "".join(chunks),
        usage=usage,
        provider_session_id=(provider_session_id if isinstance(provider_session_id, str) else None),
    )


class OmnigentSession:
    """One configured Omnigent executor and provider conversation."""

    def __init__(
        self,
        *,
        driver: OmnigentDriver,
        spec: AgentSessionSpec,
        executor: Any,  # noqa: ANN401
        tool_schemas: list[dict[str, Any]],
    ) -> None:
        """Own ``executor`` until this session is closed."""
        self._driver = driver
        self._spec = spec
        self._executor = executor
        self._tool_schemas = tool_schemas
        self._loop = asyncio.new_event_loop()
        self._state_lock = threading.Lock()
        self._turn_lock = threading.Lock()
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._serve_loop,
            name=f"vibesys-omnigent-{spec.role}",
            daemon=True,
        )
        self._active_turn: (
            concurrent.futures.Future[tuple[AgentTurnResult | None, BaseException | None]] | None
        ) = None
        self._close_finished = threading.Event()
        self._close_error: BaseException | None = None
        self._closing_thread: int | None = None
        self._closed = False
        self._failed = False
        loop_thread_started = False
        try:
            self._loop_thread.start()
            loop_thread_started = True
            if not self._loop_ready.wait(timeout=_SESSION_SHUTDOWN_TIMEOUT):
                raise RuntimeError(  # noqa: TRY301  # normalized below with resource cleanup
                    "Omnigent session event loop did not start"
                )
        except BaseException as error:
            if loop_thread_started:
                with contextlib.suppress(BaseException):
                    self._loop.call_soon_threadsafe(self._loop.stop)
                self._loop_thread.join()
            if not self._loop_thread.is_alive():
                with contextlib.suppress(BaseException):
                    self._loop.close()
            try:
                self._driver.close_executor(self._executor)
            except BaseException as cleanup_error:  # noqa: BLE001  # preserve setup error
                error.add_note(f"Omnigent session setup cleanup also failed: {cleanup_error}")
            raise

    def run_turn(
        self,
        request: AgentTurnRequest,
        observer: AgentObserver | None = None,
    ) -> AgentTurnResult:
        """Run one resumable Omnigent turn."""
        # A session is one provider conversation and remains sequential. Other
        # sessions own other loops, so an experiment chat can run concurrently
        # with the optimizer without racing one driver-global event loop.
        with self._turn_lock:
            with self._state_lock:
                if self._closed:
                    raise RuntimeError("Omnigent session is closed")
                if self._failed:
                    raise RuntimeError("Omnigent session must be reset after a failed turn")
            try:
                environment = dict(self._spec.environment)
                with (
                    _leased_session_environment(environment),
                    _patched_environ(environment),
                ):
                    with self._state_lock:
                        if self._closed:
                            raise RuntimeError(  # noqa: TRY301
                                "Omnigent session is closed"
                            )
                        future = asyncio.run_coroutine_threadsafe(
                            self._run_turn(request, observer),
                            self._loop,
                        )
                        self._active_turn = future
                    try:
                        return _unwrap_turn(future.result())
                    except BaseException:
                        future.cancel()
                        with contextlib.suppress(
                            concurrent.futures.CancelledError,
                            concurrent.futures.TimeoutError,
                            Exception,
                        ):
                            future.result(timeout=_SESSION_SHUTDOWN_TIMEOUT)
                        raise
                    finally:
                        with self._state_lock:
                            if self._active_turn is future:
                                self._active_turn = None
            except BaseException:
                with self._state_lock:
                    self._failed = True
                raise

    def close(self) -> None:
        """Release the executor exactly once."""
        active: concurrent.futures.Future[Any] | None = None
        closing_thread: int | None = None
        with self._state_lock:
            if self._closed:
                owner = False
                closing_thread = self._closing_thread
            else:
                self._closed = True
                self._closing_thread = threading.get_ident()
                active = self._active_turn
                owner = True
        if not owner:
            if closing_thread == threading.get_ident():
                return
            self._close_finished.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        first_error: BaseException | None = None
        try:
            first_error = self._close_resources(active)
        except BaseException as exc:  # noqa: BLE001  # completion must still be signaled
            first_error = exc
        finally:
            with self._state_lock:
                self._close_error = first_error
                self._close_finished.set()
        if first_error is not None:
            raise first_error

    def _close_resources(  # noqa: C901  # cleanup must continue after every failure
        self,
        active: concurrent.futures.Future[Any] | None,
    ) -> BaseException | None:
        """Best-effort all resources and preserve the first cleanup failure."""
        if active is not None:
            active.cancel()

        first_error: BaseException | None = None
        try:
            cleanup = asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop)
            try:
                first_error = cleanup.result()
            except BaseException as exc:  # noqa: BLE001
                first_error = exc
                cleanup.cancel()
        except BaseException as exc:  # noqa: BLE001
            first_error = exc
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except BaseException as exc:  # noqa: BLE001
            if first_error is None:
                first_error = exc
        self._loop_thread.join()
        if self._loop_thread.is_alive() and first_error is None:
            first_error = RuntimeError("Omnigent session event loop did not stop")
        if not self._loop_thread.is_alive():
            try:
                self._loop.close()
            except BaseException as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        try:
            self._driver.release_session(self, self._executor)
        except BaseException as exc:  # noqa: BLE001
            if first_error is None:
                first_error = exc
        return first_error

    async def _run_turn(
        self,
        request: AgentTurnRequest,
        observer: AgentObserver | None,
    ) -> tuple[AgentTurnResult | None, BaseException | None]:
        try:
            turn = _drive_turn(
                self._executor,
                request=request,
                reasoning_effort=self._spec.reasoning_effort,
                tool_schemas=self._tool_schemas,
                observer=observer,
            )
            if request.timeout is not None:
                return (
                    await asyncio.wait_for(turn, timeout=request.timeout.total_seconds()),
                    None,
                )
            return await turn, None
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            # asyncio deliberately re-raises KeyboardInterrupt/SystemExit out
            # of tasks. Encode it so it is re-raised on the invoking thread
            # without terminating this session's loop thread.
            return None, exc

    async def _shutdown(self) -> BaseException | None:
        """Cancel loop work and drain async resources before the loop closes."""
        first_error: BaseException | None = None
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        try:
            close = getattr(self._executor, "close", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
        except BaseException as exc:  # noqa: BLE001
            first_error = exc
        for cleanup in (self._loop.shutdown_asyncgens, self._loop.shutdown_default_executor):
            try:
                await cleanup()
            except BaseException as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        return first_error

    def _serve_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()


class OmnigentDriver:
    """Create sandboxed Omnigent sessions and own their native resources."""

    _CAPABILITIES = AgentCapabilities(
        timeouts=True,
        session_reuse=True,
    )

    def __init__(self) -> None:
        """Create a driver with no live sessions."""
        self._sessions: set[OmnigentSession] = set()
        self._executor_scratch: dict[int, tempfile.TemporaryDirectory[str]] = {}
        self._executor_os_environments: dict[int, tuple[Any, ...]] = {}
        self._lifecycle = threading.Condition()
        self._creating_sessions = 0
        self._close_finished = threading.Event()
        self._close_error: BaseException | None = None
        self._closing_thread: int | None = None
        self._closed = False

    @property
    def capabilities(self) -> AgentCapabilities:
        """Describe the requirements Omnigent 0.10.0 can satisfy."""
        return self._CAPABILITIES

    def create_session(self, spec: AgentSessionSpec) -> OmnigentSession:
        """Validate setup requirements and create a confined session."""
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("Omnigent driver is closed")
            self._creating_sessions += 1
        try:
            self._validate_spec(spec)
            executor, schemas = self._build_executor(spec)
            session = OmnigentSession(
                driver=self,
                spec=spec,
                executor=executor,
                tool_schemas=schemas,
            )
            with self._lifecycle:
                closed = self._closed
                if not closed:
                    self._sessions.add(session)
            if closed:
                session.close()
                raise RuntimeError("Omnigent driver is closed")
            return session
        finally:
            with self._lifecycle:
                self._creating_sessions -= 1
                self._lifecycle.notify_all()

    def close(self) -> None:  # noqa: C901  # cleanup must signal after every failure
        """Close every outstanding session."""
        closing_thread: int | None = None
        with self._lifecycle:
            if self._closed:
                owner = False
                closing_thread = self._closing_thread
            else:
                self._closed = True
                self._closing_thread = threading.get_ident()
                owner = True
        if not owner:
            if closing_thread == threading.get_ident():
                return
            self._close_finished.wait()
            if self._close_error is not None:
                raise self._close_error
            return
        first_error: BaseException | None = None
        try:
            with self._lifecycle:
                while self._creating_sessions > 0:
                    self._lifecycle.wait()
                sessions = tuple(self._sessions)
            for session in sessions:
                try:
                    session.close()
                except BaseException as exc:  # noqa: BLE001
                    if first_error is None:
                        first_error = exc
        except BaseException as exc:  # noqa: BLE001  # completion must still be signaled
            first_error = exc
        finally:
            with self._lifecycle:
                self._close_error = first_error
                self._close_finished.set()
        if first_error is not None:
            raise first_error

    def _validate_spec(self, spec: AgentSessionSpec) -> None:
        _resolve_executor_spec(spec.provider)
        if spec.mcp_servers:
            names = [server.name for server in spec.mcp_servers]
            raise OmnigentDriverError(f"Omnigent cannot install session MCP servers: {names}")
        if spec.policy.host_resources:
            raise OmnigentDriverError("Omnigent cannot enforce VibeSys host-resource grants")
        if spec.policy.containerized:
            raise OmnigentDriverError(
                "Omnigent cannot run this agent in VibeSys's container execution path"
            )
        project_paths = spec.policy.project_paths
        read_only_paths = () if project_paths is None else project_paths.read_only_paths
        unsupported_paths = [path for path in read_only_paths if not _is_top_level_dot_path(path)]
        if unsupported_paths:
            raise OmnigentDriverError(
                "Omnigent 0.10.0 can accept only top-level dot paths as "
                "contract-protected read-only project paths; unsupported paths: "
                f"{[str(path) for path in unsupported_paths]}"
            )

    def run_awaitable(self, awaitable: Any) -> Any:  # noqa: ANN401
        """Run isolated cleanup that does not belong to a live session."""
        return asyncio.run(awaitable)

    def _executor_class(self, spec: OmnigentExecutorSpec) -> type[Any]:
        try:
            module = import_module(spec.module)
        except ImportError as exc:
            raise _missing_omnigent(spec.module, exc) from exc
        try:
            return getattr(module, spec.class_name)
        except AttributeError as exc:
            raise OmnigentDriverError(
                f"Omnigent module {spec.module!r} has no {spec.class_name!r}; "
                "this integration requires the Omnigent 0.10.0 executor API"
            ) from exc

    def _build_os_env(
        self,
        spec: AgentSessionSpec,
        *,
        additional_write_paths: tuple[Path, ...] = (),
        env_passthrough: tuple[str, ...] = (),
        include_toolchain: bool = False,
    ) -> Any:  # noqa: ANN401
        try:
            from omnigent.inner.datamodel import OSEnvSandboxSpec, OSEnvSpec  # noqa: PLC0415
        except ImportError as exc:
            raise _missing_omnigent("Omnigent OS-environment datamodel", exc) from exc
        workspace = spec.workspace
        project_paths = spec.policy.project_paths
        hidden = set(() if project_paths is None else project_paths.hidden_paths)
        allow_hidden = [
            entry.name
            for entry in sorted(workspace.iterdir(), key=lambda path: path.name)
            if entry.name.startswith(".")
            and entry.name not in _OMNIGENT_INTERNAL_HIDDEN
            and Path(entry.name) not in hidden
        ]
        environment = {**os.environ, **dict(spec.environment)}
        read_paths = None
        if include_toolchain:
            resources = declare_active_rust_toolchain_resources(
                HostResourceContext(env=environment),
                workspace=workspace,
            )
            read_paths = sorted(
                {
                    str(resource.path.expanduser().resolve())
                    for resource in resources
                    if resource.path.exists()
                }
            )
        sandbox = OSEnvSandboxSpec(
            type=_sandbox_backend_for_platform(),
            read_paths=read_paths,
            write_paths=[str(workspace), *(str(path) for path in additional_write_paths)],
            cwd_allow_hidden=allow_hidden,
            cwd_hidden_scan_recursive=False,
            cwd_hidden_scan_overflow="error",
            mask_paths=sorted({str(path) for path in hidden} | _OMNIGENT_INTERNAL_HIDDEN),
            env_passthrough=list(env_passthrough) or None,
        )
        return OSEnvSpec(
            type="caller_process",
            cwd=str(workspace),
            sandbox=sandbox,
        )

    def _build_executor(self, spec: AgentSessionSpec) -> tuple[Any, list[dict[str, Any]]]:
        executor_spec = _resolve_executor_spec(spec.provider)
        executor_cls = self._executor_class(executor_spec)
        os_env_spec = self._build_os_env(spec)
        scratch = tempfile.TemporaryDirectory(prefix="vibesys-omnigent-")
        scratch_path = Path(scratch.name)
        environment = {**os.environ, **dict(spec.environment)}
        tool_environment = dict(spec.environment)
        rust_toolchain = resolve_active_rust_toolchain(
            HostResourceContext(env=environment),
            workspace=spec.workspace,
        )
        if rust_toolchain is not None:
            rust_bin = rust_toolchain[0] / "bin"
            tool_environment.update(
                {
                    "CARGO_HOME": str(scratch_path / "cargo-home"),
                    "PATH": os.pathsep.join((str(rust_bin), environment.get("PATH", ""))),
                    "RUSTC": str(rust_bin / "rustc"),
                    "RUSTDOC": str(rust_bin / "rustdoc"),
                    "RUSTUP_AUTO_INSTALL": "0",
                }
            )
        try:
            shell_os_env_spec = self._build_os_env(
                spec,
                additional_write_paths=(scratch_path,),
                env_passthrough=tuple(tool_environment),
                include_toolchain=True,
            )
        except BaseException:
            scratch.cleanup()
            raise
        executor_kwargs: dict[str, Any] = {
            "cwd": str(spec.workspace),
            "model": spec.model,
            "os_env": os_env_spec,
        }
        if spec.provider == "codex":
            # Codex's native workspace sandbox cannot represent Omnigent's
            # dot-path masks. Route all filesystem access through the
            # sandboxed sys_os_* tools instead.
            executor_kwargs["disable_native_tools"] = True
        try:
            executor = executor_cls(**executor_kwargs)
        except ImportError as exc:
            scratch.cleanup()
            raise OmnigentDriverError(
                f"Omnigent provider {spec.provider!r} is unavailable: {exc}"
            ) from exc
        except BaseException:
            scratch.cleanup()
            raise
        if not hasattr(executor, _TOOL_EXECUTOR_ATTR):
            with contextlib.suppress(Exception):
                self.close_executor(executor)
            scratch.cleanup()
            raise OmnigentDriverError(
                f"{executor_cls.__name__} has no {_TOOL_EXECUTOR_ATTR!r} slot; "
                "this integration requires the private Omnigent 0.10.0 tool-dispatch seam"
            )
        try:
            schemas, dispatch, os_environments = _build_os_tools(
                os_env_spec,
                spec.workspace,
                tool_environment,
                shell_os_env_spec,
            )
        except BaseException:
            with contextlib.suppress(Exception):
                self.close_executor(executor)
            scratch.cleanup()
            raise
        setattr(executor, _TOOL_EXECUTOR_ATTR, dispatch)
        with self._lifecycle:
            self._executor_scratch[id(executor)] = scratch
            self._executor_os_environments[id(executor)] = os_environments
        return executor, schemas

    def release_session(
        self,
        session: OmnigentSession,
        executor: Any,  # noqa: ANN401
    ) -> None:
        """Forget a closed session and release its synchronous scratch state."""
        with self._lifecycle:
            self._sessions.discard(session)
            scratch = self._executor_scratch.pop(id(executor), None)
            os_environments = self._executor_os_environments.pop(id(executor), ())
        first_error: BaseException | None = None
        for os_environment in os_environments:
            try:
                os_environment.close()
            except BaseException as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        if scratch is not None:
            try:
                scratch.cleanup()
            except BaseException as exc:  # noqa: BLE001
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def close_executor(
        self,
        executor: Any,  # noqa: ANN401
        *,
        run_awaitable: Callable[[Any], Any] | None = None,
    ) -> None:
        """Close a native executor, awaiting asynchronous cleanup when needed."""
        try:
            close = getattr(executor, "close", None)
            if close is None:
                return
            result = close()
            if asyncio.iscoroutine(result):
                (run_awaitable or self.run_awaitable)(result)
        finally:
            with self._lifecycle:
                scratch = self._executor_scratch.pop(id(executor), None)
                os_environments = self._executor_os_environments.pop(id(executor), ())
            for os_environment in os_environments:
                with contextlib.suppress(BaseException):
                    os_environment.close()
            if scratch is not None:
                scratch.cleanup()
