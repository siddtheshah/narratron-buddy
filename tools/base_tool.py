import asyncio
import concurrent.futures
import functools
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger(__name__)


def logged_tool_call(func: Callable):
    """Log a public tool invocation without changing its cooldown behavior."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.log_tool_call(func.__name__)
        return func(self, *args, **kwargs)

    return wrapper


def single_flight(
    func=None,
    *,
    timeout: Optional[float] = None,
    on_timeout: Optional[Any] = None,
    error_message: Optional[str] = None,
    return_dict_on_error: bool = True,
):
    """Decorator ensuring only one invocation of a tool runs at a time, with optional timeout.

    Can be used as:
        @single_flight
        def my_tool(self, ...): ...

    or:
        @single_flight(timeout=20.0, on_timeout="restart_planner_agent")
        def my_tool(self, ...): ...
    """
    def decorator(fn: Callable):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(self, *args, **kwargs):
                tool_name = fn.__name__
                if hasattr(self, "acquire_in_flight"):
                    if not self.acquire_in_flight(tool_name):
                        msg = error_message or f"{tool_name} is already in progress. Please wait for it to complete."
                        return {"error": msg} if return_dict_on_error else f"Error: {msg}"

                resolved_timeout = timeout
                if resolved_timeout is None and hasattr(self, "user_action_timeout_seconds"):
                    resolved_timeout = getattr(self, "user_action_timeout_seconds")

                def trigger_timeout():
                    if on_timeout:
                        if callable(on_timeout):
                            on_timeout(self)
                        elif isinstance(on_timeout, str) and hasattr(self, on_timeout):
                            cb = getattr(self, on_timeout)
                            if callable(cb):
                                cb()

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
                    if hasattr(self, "release_in_flight"):
                        self.release_in_flight(tool_name)

            return async_wrapper
        else:
            @functools.wraps(fn)
            def wrapper(self, *args, **kwargs):
                tool_name = fn.__name__
                if hasattr(self, "acquire_in_flight"):
                    if not self.acquire_in_flight(tool_name):
                        msg = error_message or f"{tool_name} is already in progress. Please wait for it to complete."
                        return {"error": msg} if return_dict_on_error else f"Error: {msg}"

                resolved_timeout = timeout
                if resolved_timeout is None and hasattr(self, "user_action_timeout_seconds"):
                    resolved_timeout = getattr(self, "user_action_timeout_seconds")

                def trigger_timeout():
                    if on_timeout:
                        if callable(on_timeout):
                            on_timeout(self)
                        elif isinstance(on_timeout, str) and hasattr(self, on_timeout):
                            cb = getattr(self, on_timeout)
                            if callable(cb):
                                cb()

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
                    if hasattr(self, "release_in_flight"):
                        self.release_in_flight(tool_name)

            return wrapper

    if callable(func):
        return decorator(func)
    return decorator


def with_cooldown(
    func_or_desc=None,
    action_desc: Optional[str] = None,
    duration: Optional[Any] = None,
):
    """Decorator annotation for BaseTools methods that enforces cooldown tracking.

    Can be used as:
        @with_cooldown
        def my_tool(self, ...): ...

    or:
        @with_cooldown("doing something")
        def my_tool(self, ...): ...

    A method can override the suite's default cooldown duration:
        @with_cooldown("doing something", duration=4.0)
        def my_tool(self, ...): ...
    """
    if callable(func_or_desc):
        func = func_or_desc
        desc = action_desc

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            tool_name = func.__name__
            self.log_tool_call(tool_name)
            cooldown_err = self.check_cooldown(tool_name, desc, duration)
            if cooldown_err:
                trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                if callable(trigger_cb):
                    trigger_cb(tool_name)
                return cooldown_err

            result = func(self, *args, **kwargs)
            if not (isinstance(result, str) and result.startswith("Error:")):
                self.record_tool_call(tool_name, duration)
            return result

        return wrapper
    else:
        desc = func_or_desc or action_desc

        def decorator(func: Callable):
            @functools.wraps(func)
            def wrapper(self, *args, **kwargs):
                tool_name = func.__name__
                self.log_tool_call(tool_name)
                cooldown_err = self.check_cooldown(tool_name, desc, duration)
                if cooldown_err:
                    trigger_cb = getattr(self, "_trigger_after_tool_call", None)
                    if callable(trigger_cb):
                        trigger_cb(tool_name)
                    return cooldown_err

                result = func(self, *args, **kwargs)
                if not (isinstance(result, str) and result.startswith("Error:")):
                    self.record_tool_call(tool_name, duration)
                return result
            return wrapper

        return decorator


class BaseTools:
    """Base class for all agent tool suites providing unified theater management and cooldown tracking."""

    def __init__(
        self,
        config: dict = None,
        theater_id: str = "",
        canvas_state_service: Any = None,
        default_cooldown: float = 0.0,
    ):
        self.config: dict = config or {}
        self._active_theater_id: str = theater_id
        self.canvas_state_service: Any = canvas_state_service

        # Callback hooks
        self.on_cooldown_expired: Optional[Callable[[str], None]] = None
        self.on_after_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None

        # Internal tracking per tool name
        self._last_call_times: Dict[str, float] = {}
        self._cooldown_timers: Dict[str, threading.Timer] = {}
        self._in_flight_tools: Set[str] = set()
        self._in_flight_lock = threading.Lock()

        # Determine cooldown duration directly from tool subconfig
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", default_cooldown))

    @property
    def theater_id(self) -> str:
        return self._active_theater_id

    @theater_id.setter
    def theater_id(self, value: str) -> None:
        self._active_theater_id = value

    @property
    def active_theater_id(self) -> str:
        return self._active_theater_id

    @active_theater_id.setter
    def active_theater_id(self, value: str) -> None:
        self._active_theater_id = value

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
            desc_text = action_desc or "executing this action again"
            return (
                f"Error: {tool_name} is on cooldown. "
            )
        return None

    def log_tool_call(self, tool_name: str) -> None:
        """Emit the single INFO-level record that marks a public tool call."""
        logger.info("[%s] %s called (theater=%s).", self.__class__.__name__, tool_name, self.active_theater_id or "default")

    def record_tool_call(self, tool_name: str, duration: Optional[float] = None) -> None:
        """Records the timestamp of a successful tool call and schedules the expiration timer."""
        cooldown_duration = self._resolve_cooldown_duration(duration)
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
