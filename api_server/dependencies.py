"""Late-bound access to application services registered in ``object_registry``.

Routes hold these small proxies instead of concrete service instances.  Tests
can therefore patch ``object_registry.db`` (or any other service) and exercise
the real endpoint code without reloading the FastAPI application.
"""

from __future__ import annotations

from typing import Any

import object_registry


class RegistryDependency:
    """Delegate attribute access to the current value of a registry entry."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    @property
    def target(self) -> Any:
        return getattr(object_registry, object.__getattribute__(self, "_name"))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self.target, name, value)

    def __bool__(self) -> bool:
        return bool(self.target)

    def __repr__(self) -> str:
        return f"RegistryDependency({object.__getattribute__(self, '_name')!r})"


FLAGS = RegistryDependency("FLAGS")
agent_manager = RegistryDependency("agent_manager")
canvas_states = RegistryDependency("canvas_states")
db = RegistryDependency("db")
pricing_controller = RegistryDependency("pricing_controller")
theater_manager = RegistryDependency("theater_manager")
