"""Serialize native page edits and game commits under the same reentrant lock."""
from _thread import RLock
from collections.abc import Callable
from functools import wraps
from typing import Concatenate, ParamSpec, Protocol, TypeVar


class CanvasLockOwner(Protocol):
    canvas_lock: RLock


Owner = TypeVar("Owner", bound=CanvasLockOwner)
Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


def synchronized(method: Callable[Concatenate[Owner, Parameters], Result]) -> Callable[Concatenate[Owner, Parameters], Result]:
    @wraps(method)
    def call(self: Owner, *args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
        with self.canvas_lock:
            return method(self, *args, **kwargs)

    return call
