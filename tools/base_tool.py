import asyncio
import concurrent.futures
import functools
import inspect
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Set

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater

logger = logging.getLogger(__name__)


CANVAS_PINNED_MESSAGE = (
    "The canvas is currently pinned by the orator. Image and animation tools are "
    "temporarily unavailable; keep the current canvas visual unchanged until the orator unpins it."
)


def blocked_when_canvas_pinned(func: Callable):
    """Return a clear tool response without starting visual work while pinned."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        visual = getattr(getattr(self, "canvas_manager", None), "visual", None)
        # Require the concrete state flag so permissive MagicMock-based tool
        # fixtures (and legacy canvas implementations) remain unpinned.
        if getattr(visual, "pinned", False) is True:
            trigger_cb = getattr(self, "_trigger_after_tool_call", None)
            if callable(trigger_cb):
                trigger_cb(func.__name__)
            return CANVAS_PINNED_MESSAGE
        return func(self, *args, **kwargs)

    return wrapper


def _tool_call_arguments(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    """Return public tool arguments as named values for invocation logging."""
    try:
        bound = inspect.signature(func).bind_partial(None, *args, **kwargs)
        bound.apply_defaults()
        return {name: value for name, value in bound.arguments.items() if name != "self"}
    except (TypeError, ValueError):
        # Logging must never interfere with a tool call if its signature cannot
        # be introspected (for example, a dynamically supplied callable).
        return {"args": args, "kwargs": kwargs}


def logged_tool_call(func: Callable):
    """Log a public tool invocation without changing its cooldown behavior."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.log_tool_call(func.__name__, _tool_call_arguments(func, args, kwargs))
        return func(self, *args, **kwargs)

    return wrapper


def single_flight(
    func=None,
    *,
    timeout: Optional[float] = None,
    on_timeout: Optional[Callable[[Any], None]] = None,
    error_message: Optional[str] = None,
    hold_until_released: bool = False,
):
    """Decorator ensuring only one invocation of a tool runs at a time, with optional timeout.

    Can be used as:
        @single_flight
        def my_tool(self, ...): ...

    or:
        @single_flight(timeout=20.0, on_timeout=lambda tool: tool.restart_planner_agent())
        def my_tool(self, ...): ...

    Set ``hold_until_released=True`` for a tool that starts background work.
    That worker must later call ``release_in_flight(<tool name>)`` in a finally
    block, so the flight covers the real operation rather than only dispatch.
    """
    if on_timeout is not None and not callable(on_timeout):
        raise TypeError("single_flight on_timeout must be a callable accepting the tool instance.")

    def decorator(fn: Callable):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(self, *args, **kwargs):
                tool_name = fn.__name__
                if hasattr(self, "acquire_in_flight"):
                    if not self.acquire_in_flight(tool_name):
                        msg = error_message or f"{tool_name} is already in progress. Please wait for it to complete."
                        return msg

                resolved_timeout = timeout
                if resolved_timeout is None and hasattr(self, "user_action_timeout_seconds"):
                    resolved_timeout = getattr(self, "user_action_timeout_seconds")

                def trigger_timeout():
                    if on_timeout is not None:
                        on_timeout(self)

                try:
                    if resolved_timeout is not None and resolved_timeout > 0:
                        try:
                            return await asyncio.wait_for(fn(self, *args, **kwargs), timeout=resolved_timeout)
                        except (asyncio.TimeoutError, TimeoutError) as exc:
                            logger.error(f"[{self.__class__.__name__}] {tool_name} timed out after {resolved_timeout}s")
                            trigger_timeout()
                            raise TimeoutError(f"{tool_name} timed out after {resolved_timeout} seconds.") from exc
                    else:
                        return await fn(self, *args, **kwargs)
                finally:
                    if not hold_until_released and hasattr(self, "release_in_flight"):
                        self.release_in_flight(tool_name)

            return async_wrapper
        else:
            @functools.wraps(fn)
            def wrapper(self, *args, **kwargs):
                tool_name = fn.__name__
                if hasattr(self, "acquire_in_flight"):
                    if not self.acquire_in_flight(tool_name):
                        msg = error_message or f"{tool_name} is already in progress. Please wait for it to complete."
                        return msg

                resolved_timeout = timeout
                if resolved_timeout is None and hasattr(self, "user_action_timeout_seconds"):
                    resolved_timeout = getattr(self, "user_action_timeout_seconds")

                def trigger_timeout():
                    if on_timeout is not None:
                        on_timeout(self)

                try:
                    if resolved_timeout is not None and resolved_timeout > 0:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                            future = executor.submit(fn, self, *args, **kwargs)
                            try:
                                return future.result(timeout=resolved_timeout)
                            except concurrent.futures.TimeoutError as exc:
                                logger.error(f"[{self.__class__.__name__}] {tool_name} timed out after {resolved_timeout}s")
                                trigger_timeout()
                                raise TimeoutError(f"{tool_name} timed out after {resolved_timeout} seconds.") from exc
                    else:
                        return fn(self, *args, **kwargs)
                finally:
                    if not hold_until_released and hasattr(self, "release_in_flight"):
                        self.release_in_flight(tool_name)

            return wrapper

    if callable(func):
        return decorator(func)
    return decorator


def with_cooldown(
    func_or_desc=None,
    action_desc: Optional[str] = None,
    duration: Optional[Any] = None,
    tool_name: Optional[str] = None,
):
    """Decorator annotation for BaseTools methods that enforces cooldown tracking.

    Can be used as:
        @with_cooldown
        def my_tool(self, ...): ...

    or:
        @with_cooldown(action_desc="doing something")
        def my_tool(self, ...): ...

    A method can override the suite's default cooldown duration:
        @with_cooldown(action_desc="doing something", duration=4.0)
        def my_tool(self, ...): ...
    """
    if callable(func_or_desc):
        func = func_or_desc
        desc = action_desc

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            cooldown_key = tool_name or func.__name__
            self.log_tool_call(cooldown_key, _tool_call_arguments(func, args, kwargs))
            cooldown_err = self.check_cooldown(cooldown_key, action_desc=desc, duration=duration)
            if cooldown_err:
                trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                if callable(trigger_cb):
                    trigger_cb(cooldown_key)
                return cooldown_err

            result = func(self, *args, **kwargs)
            if not (isinstance(result, str) and result.startswith("Error:")):
                self.record_tool_call(cooldown_key, duration=duration)
            return result

        return wrapper
    else:
        desc = func_or_desc or action_desc

        def decorator(func: Callable):
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                cooldown_key = tool_name or func.__name__
                self.log_tool_call(cooldown_key, _tool_call_arguments(func, args, kwargs))
                cooldown_err = self.check_cooldown(cooldown_key, action_desc=desc, duration=duration)
                if cooldown_err:
                    trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                    if callable(trigger_cb):
                        trigger_cb(cooldown_key)
                    return cooldown_err

                result = func(self, *args, **kwargs)
                if not (isinstance(result, str) and result.startswith("Error:")):
                    self.record_tool_call(cooldown_key, duration=duration)
                return result
            return wrapper

        return decorator


def with_cycle_cooldown(
    func_or_desc=None,
    action_desc: Optional[str] = None,
    duration: Optional[Any] = None,
    tool_name: Optional[str] = None,
):
    """Decorator annotation for BaseTools methods that schedules calls on cooldown instead of blocking.

    If invoked while on cooldown, the call is scheduled to execute automatically
    as soon as the cooldown finishes. If another call arrives before the cycle
    finishes, it updates the parameters of the scheduled call without enqueuing
    an additional call (matching visual cycle behavior).
    """
    def _create_wrapper(func: Callable):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(self, *args, **kwargs):
                cooldown_key = tool_name or func.__name__
                self.log_tool_call(cooldown_key, _tool_call_arguments(func, args, kwargs))
                is_scheduled, msg = self.handle_cycle_call(
                    cooldown_key, func, args, kwargs, duration=duration
                )
                if is_scheduled:
                    trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                    if callable(trigger_cb):
                        trigger_cb(cooldown_key)
                    return msg

                self._last_call_times[cooldown_key] = time.time()
                try:
                    result = await func(self, *args, **kwargs)
                    if not (isinstance(result, str) and result.startswith("Error:")):
                        self.record_tool_call(cooldown_key, duration=duration)
                    else:
                        self._last_call_times.pop(cooldown_key, None)
                    return result
                except Exception:
                    self._last_call_times.pop(cooldown_key, None)
                    raise

            return async_wrapper
        else:
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                cooldown_key = tool_name or func.__name__
                self.log_tool_call(cooldown_key, _tool_call_arguments(func, args, kwargs))
                is_scheduled, msg = self.handle_cycle_call(
                    cooldown_key, func, args, kwargs, duration=duration
                )
                if is_scheduled:
                    trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                    if callable(trigger_cb):
                        trigger_cb(cooldown_key)
                    return msg

                self._last_call_times[cooldown_key] = time.time()
                try:
                    result = func(self, *args, **kwargs)
                    if not (isinstance(result, str) and result.startswith("Error:")):
                        self.record_tool_call(cooldown_key, duration=duration)
                    else:
                        self._last_call_times.pop(cooldown_key, None)
                    return result
                except Exception:
                    self._last_call_times.pop(cooldown_key, None)
                    raise

            return wrapper

    if callable(func_or_desc):
        return _create_wrapper(func_or_desc)
    else:
        return _create_wrapper


class BaseTools:
    """Base class for all agent tool suites providing unified theater management and cooldown tracking."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
    ) -> None:
        self.theater = theater
        self.canvas_manager = canvas_manager
        self.config = theater.config() or {}

        # Callback hooks
        self.on_cooldown_expired: Optional[Callable[[str], None]] = None
        self.on_after_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None

        # Internal tracking per tool name
        self._last_call_times: Dict[str, float] = {}
        self._cooldown_timers: Dict[str, threading.Timer] = {}
        self._active_cooldown_durations: Dict[str, float] = {}
        self._pending_cycle_calls: Dict[str, Dict[str, Any]] = {}
        self._cycle_cooldown_lock = threading.Lock()
        self._in_flight_tools: Set[str] = set()
        self._in_flight_lock = threading.Lock()

        # Determine cooldown duration directly from tool subconfig
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", 0.0))

    @property
    def theater_id(self) -> str:
        return self.theater.theater_id

    @property
    def active_theater_id(self) -> str:
        return self.theater_id

    def is_in_flight(self, tool_name: str) -> bool:
        """Return True if the specified tool is currently executing."""
        with self._in_flight_lock:
            return tool_name in self._in_flight_tools

    def acquire_in_flight(self, tool_name: str) -> bool:
        """Attempt to mark a tool as in-flight. Returns True if acquired, False if already in-flight."""
        with self._in_flight_lock:
            if tool_name in self._in_flight_tools:
                return False
            self._in_flight_tools.add(tool_name)
            return True

    def release_in_flight(self, tool_name: str) -> None:
        """Release the in-flight status for a tool."""
        with self._in_flight_lock:
            self._in_flight_tools.discard(tool_name)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("last_") and name.endswith("_time"):
            action = name[5:-5]
            for tool_name, call_time in getattr(self, "_last_call_times", {}).items():
                if tool_name == action or tool_name.startswith(f"{action}_"):
                    return call_time
            return 0.0
        if name.startswith("_") and name.endswith("_cooldown_timer"):
            action = name[1:-15]
            for tool_name, timer in getattr(self, "_cooldown_timers", {}).items():
                if tool_name == action or tool_name.startswith(f"{action}_"):
                    return timer
            return None
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("last_") and name.endswith("_time") and hasattr(self, "_last_call_times"):
            action = name[5:-5]
            for tool_name in list(self._last_call_times.keys()):
                if tool_name == action or tool_name.startswith(f"{action}_"):
                    self._last_call_times[tool_name] = float(value)
                    return
            target_key = f"{action}_image" if action in ("create", "show") else (f"{action}_music" if action == "play" else action)
            self._last_call_times[target_key] = float(value)
            return
        if name.startswith("_") and name.endswith("_cooldown_timer") and hasattr(self, "_cooldown_timers"):
            action = name[1:-15]
            target_key = f"{action}_image" if action in ("create", "show") else (f"{action}_music" if action == "play" else action)
            if value:
                self._cooldown_timers[target_key] = value
            else:
                self._cooldown_timers.pop(target_key, None)
            return
        super().__setattr__(name, value)

    def get_cooldown_remaining(
        self,
        tool_name: str,
        duration: Optional[Any] = None,
    ) -> float:
        """Return the number of seconds remaining on cooldown for tool_name, or 0.0 if not on cooldown."""
        durations = getattr(self, "_active_cooldown_durations", {})
        cooldown_duration = durations.get(tool_name, self._resolve_cooldown_duration(duration))
        now = time.time()
        last_time = getattr(self, "_last_call_times", {}).get(tool_name, 0.0)
        elapsed = now - last_time
        remaining = cooldown_duration - elapsed
        return max(0.0, float(remaining))

    def handle_cycle_call(
        self,
        tool_name: str,
        func: Callable,
        args: tuple,
        kwargs: dict,
        duration: Optional[Any] = None,
    ) -> tuple[bool, Any]:
        """Check cooldown and either schedule/update for next cycle or indicate immediate run."""
        lock = getattr(self, "_cycle_cooldown_lock", None)
        if lock is None:
            self._cycle_cooldown_lock = threading.Lock()
            self._pending_cycle_calls = {}
            self._active_cooldown_durations = {}
            lock = self._cycle_cooldown_lock

        with lock:
            remaining = self.get_cooldown_remaining(tool_name, duration=duration)
            if remaining <= 0.0:
                return (False, None)

            is_update = tool_name in self._pending_cycle_calls
            self._pending_cycle_calls[tool_name] = {
                "func": func,
                "args": args,
                "kwargs": kwargs,
                "duration": duration,
            }

            existing_timer = getattr(self, "_cooldown_timers", {}).get(tool_name)
            if not existing_timer or not existing_timer.is_alive():
                self._schedule_cooldown_timer(tool_name, remaining)

            if is_update:
                msg = f"Tool '{tool_name}' parameters updated for next cycle."
            else:
                msg = f"Tool '{tool_name}' scheduled for next cycle when cooldown expires."

            logger.info("[%s] %s", self.__class__.__name__, msg)
            return (True, msg)

    def get_pending_cycle_call(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Return the currently pending cycle call details for tool_name, if any."""
        lock = getattr(self, "_cycle_cooldown_lock", None)
        if lock is None:
            return None
        with lock:
            pending = getattr(self, "_pending_cycle_calls", {}).get(tool_name)
            return dict(pending) if pending else None

    def cancel_pending_cycle_call(self, tool_name: str) -> bool:
        """Cancel any pending cycle call for tool_name. Returns True if cancelled."""
        lock = getattr(self, "_cycle_cooldown_lock", None)
        if lock is None:
            return False
        with lock:
            pending_calls = getattr(self, "_pending_cycle_calls", None)
            if pending_calls is not None:
                return pending_calls.pop(tool_name, None) is not None
            return False

    def _execute_pending_cycle_call(self, tool_name: str) -> bool:
        """Execute the scheduled cycle call if one exists. Returns True if executed."""
        lock = getattr(self, "_cycle_cooldown_lock", None)
        if lock is None:
            return False

        with lock:
            pending_calls = getattr(self, "_pending_cycle_calls", None)
            if not pending_calls:
                return False
            pending = pending_calls.pop(tool_name, None)
            if not pending:
                return False

        func = pending["func"]
        args = pending["args"]
        kwargs = pending["kwargs"]
        duration = pending.get("duration")

        logger.info(
            "[%s] Executing scheduled cycle tool call for '%s' with args=%s, kwargs=%s",
            self.__class__.__name__,
            tool_name,
            args,
            kwargs,
        )
        self._last_call_times[tool_name] = time.time()
        try:
            self.log_tool_call(tool_name, _tool_call_arguments(func, args, kwargs))
            if asyncio.iscoroutinefunction(func):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(func(self, *args, **kwargs), loop)
                    result = future.result()
                else:
                    result = asyncio.run(func(self, *args, **kwargs))
            else:
                result = func(self, *args, **kwargs)

            if not (isinstance(result, str) and result.startswith("Error:")):
                self.record_tool_call(tool_name, duration=duration)
            else:
                self._last_call_times.pop(tool_name, None)

            trigger_cb = getattr(self, "_trigger_after_tool_call", None)
            if callable(trigger_cb):
                trigger_cb(tool_name)
            return True
        except Exception as e:
            self._last_call_times.pop(tool_name, None)
            logger.error(
                "[%s] Exception executing scheduled cycle tool call '%s': %s",
                self.__class__.__name__,
                tool_name,
                e,
                exc_info=True,
            )
            return False

    def check_cooldown(
        self,
        tool_name: str,
        action_desc: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Optional[str]:
        """Checks if a tool is currently on cooldown.

        If on cooldown, schedules the timer and returns an error message.
        Otherwise returns None.
        """
        cooldown_duration = self._resolve_cooldown_duration(duration)
        now = time.time()
        last_time = self._last_call_times.get(tool_name, 0.0)
        elapsed = now - last_time
        if elapsed < cooldown_duration:
            remaining_sec = float(cooldown_duration - elapsed)
            remaining = int(remaining_sec)
            self._schedule_cooldown_timer(tool_name, remaining_sec)
            logger.warning(
                f"[{self.__class__.__name__}] {tool_name} is on cooldown. Elapsed: {elapsed:.2f}s, "
                f"Cooldown: {cooldown_duration}s, Remaining: {remaining}s"
            )
            return (
                f"Error: {tool_name} is on cooldown. "
            )
        return None

    def log_tool_call(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        """Emit the single INFO-level record that marks a public tool call."""
        logger.info(
            "[%s] %s called (theater=%s, args=%s).",
            self.__class__.__name__,
            tool_name,
            self.theater_id,
            arguments or {},
        )

    def record_tool_call(self, tool_name: str, duration: Optional[float] = None) -> None:
        """Records the timestamp of a successful tool call and schedules the expiration timer."""
        cooldown_duration = self._resolve_cooldown_duration(duration)
        if hasattr(self, "_active_cooldown_durations"):
            self._active_cooldown_durations[tool_name] = cooldown_duration
        self._last_call_times[tool_name] = time.time()
        self._schedule_cooldown_timer(tool_name, cooldown_duration)

    def _resolve_cooldown_duration(self, duration: Optional[Any]) -> float:
        """Resolve a fixed or tool-suite-specific cooldown duration safely."""
        configured_duration = self.cooldown_duration if duration is None else duration
        if callable(configured_duration):
            configured_duration = configured_duration(self)
        try:
            return max(0.0, float(configured_duration))
        except (TypeError, ValueError):
            logger.warning("[%s] Invalid cooldown duration %r; using 0 seconds.", self.__class__.__name__, configured_duration)
            return 0.0

    def _schedule_cooldown_timer(self, tool_name: str, remaining_seconds: float) -> None:
        """Schedules a background timer to invoke callbacks when a tool's cooldown expires."""
        def _timer_callback():
            logger.debug(f"[{self.__class__.__name__}] Cooldown for '{tool_name}' has expired.")
            executed = self._execute_pending_cycle_call(tool_name)
            if not executed:
                cb_expired = getattr(self, "on_cooldown_expired", None)
                if cb_expired:
                    try:
                        cb_expired(tool_name)
                    except Exception as e:
                        logger.error(f"[{self.__class__.__name__}] Exception in on_cooldown_expired callback: {e}")

        delay = max(0.01, remaining_seconds + 0.05)

        existing_timer = self._cooldown_timers.get(tool_name)
        if existing_timer:
            existing_timer.cancel()

        timer = threading.Timer(delay, _timer_callback)
        timer.daemon = True
        self._cooldown_timers[tool_name] = timer
        timer.start()
