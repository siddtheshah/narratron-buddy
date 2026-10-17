"""Reject browser requests that were not fulfilled by a test route."""

from collections.abc import Callable, Iterator
from functools import wraps
from typing import ParamSpec, TypeVar

from playwright.sync_api import Browser, BrowserContext, BrowserType, Page, Route, WebSocketRoute
import pytest


Parameters = ParamSpec("Parameters")
Result = TypeVar("Result")


@pytest.fixture(autouse=True)
def offline_browser_contexts(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Install a last-resort deny route before individual tests register mocks."""
    blocked: list[str] = []
    guarded: set[BrowserContext] = set()

    def deny_request(route: Route) -> None:
        blocked.append(route.request.url)
        route.abort("blockedbyclient")

    def deny_websocket(route: WebSocketRoute) -> None:
        blocked.append(route.url)
        # A routed WebSocket stays local unless connect_to_server() is called.
        # Returning also avoids re-entering Playwright's synchronous dispatcher.

    def protect(context: BrowserContext) -> None:
        if context not in guarded:
            context.route("**/*", deny_request)
            context.route_web_socket("**/*", deny_websocket)
            guarded.add(context)

    def wrap_creation(
        create: Callable[Parameters, Result],
        context_for: Callable[[Result], BrowserContext],
    ) -> Callable[Parameters, Result]:
        @wraps(create)
        def create_guarded(*args: Parameters.args, **kwargs: Parameters.kwargs) -> Result:
            kwargs["service_workers"] = "block"
            result = create(*args, **kwargs)
            protect(context_for(result))
            return result

        return create_guarded

    def page_context(page: Page) -> BrowserContext:
        return page.context

    def same_context(context: BrowserContext) -> BrowserContext:
        return context

    monkeypatch.setattr(Browser, "new_context", wrap_creation(Browser.new_context, same_context))
    monkeypatch.setattr(Browser, "new_page", wrap_creation(Browser.new_page, page_context))
    monkeypatch.setattr(
        BrowserType,
        "launch_persistent_context",
        wrap_creation(BrowserType.launch_persistent_context, same_context),
    )
    yield blocked
    assert not blocked, "Unmocked browser requests: " + ", ".join(sorted(set(blocked)))
