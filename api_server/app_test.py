import base64
import asyncio
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, WebSocket
from fastapi.testclient import TestClient
import pytest

import object_registry
from api_server.app import can_control_agent_websocket, can_access_agent_websocket, is_contributor
app_module = importlib.import_module("api_server.app")


class TestCanAccessAgentWebsocket(unittest.TestCase):
    def test_allows_owner(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={"canvas_access": "eyJzMSI6IktFWS0xMjMifQ=="}, user=None)
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_rejects_unrelated_user_without_join_key(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 7}

        self.assertFalse(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder_via_websocket_scope(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        encoded_grants = base64.urlsafe_b64encode(json.dumps({"s1": "KEY-123"}).encode("utf-8")).decode("ascii")
        request = SimpleNamespace(scope={"type": "websocket", "headers": [(b"cookie", f"canvas_access={encoded_grants}".encode("utf-8"))]})
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))


    def test_allows_active_orator(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 99}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_owner_when_co_orator_is_active(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))


class TestCanControlAgentWebsocket(unittest.TestCase):
    def test_allows_owner_when_no_baton_transfer_is_active(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        self.assertTrue(can_control_agent_websocket(deployment, current_user={"id": 42}))

    def test_allows_only_active_orator_after_baton_transfer(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        self.assertTrue(can_control_agent_websocket(deployment, current_user={"id": 99}))
        self.assertFalse(can_control_agent_websocket(deployment, current_user={"id": 42}))
        self.assertFalse(can_control_agent_websocket(deployment, current_user={"id": 7}))

    def test_rejects_join_key_holder_without_authenticated_baton(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        self.assertFalse(can_control_agent_websocket(deployment, current_user=None))


class TestIsContributor(unittest.TestCase):
    def test_allows_owner(self):
        deployment = {"theater_id": "s1", "user_id": 42, "contributors": "[]"}
        self.assertTrue(is_contributor(deployment, current_user={"id": 42}))

    def test_allows_active_orator(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "contributors": "[]"}
        self.assertTrue(is_contributor(deployment, current_user={"id": 99}))

    def test_allows_user_in_contributors_list(self):
        deployment = {"theater_id": "s1", "user_id": 42, "contributors": json.dumps([101, 102])}
        self.assertTrue(is_contributor(deployment, current_user={"id": 101}))
        self.assertTrue(is_contributor(deployment, current_user={"id": 102}))

    def test_rejects_unlisted_user(self):
        deployment = {"theater_id": "s1", "user_id": 42, "contributors": json.dumps([101])}
        self.assertFalse(is_contributor(deployment, current_user={"id": 77}))

    def test_rejects_unauthenticated_user(self):
        deployment = {"theater_id": "s1", "user_id": 42, "contributors": "[]"}
        self.assertFalse(is_contributor(deployment, current_user=None))
        self.assertFalse(is_contributor(None, current_user={"id": 42}))


def test_start_agent_stops_registry_session_when_owner_has_no_credits() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 0}
    manager = MagicMock()
    req = MagicMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 11})),
    ):
        response = asyncio.run(app_module.start_agent_endpoint("stage", req))

    assert response.status_code == 402
    manager.stop_session.assert_called_once_with(theater_id="stage")


def test_start_agent_summons_the_live_session_without_a_microphone_connection() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 3.5}
    session = MagicMock(status="active")
    manager = MagicMock()
    manager.get_or_create_session.return_value = session
    req = MagicMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 11})),
    ):
        result = asyncio.run(app_module.start_agent_endpoint("stage", req))

    assert result["agent_running"] is True
    session.summon.assert_called_once_with()


def test_start_agent_rejects_unauthenticated_request() -> None:
    req = MagicMock()
    with patch.object(app_module, "get_current_user_async", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app_module.start_agent_endpoint("stage", req))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required."


def test_start_agent_rejects_non_orator() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11, "active_orator_id": 11}
    req = MagicMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 99})),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app_module.start_agent_endpoint("stage", req))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only the active orator can start the agent."


def test_start_agent_allows_transferred_active_orator() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11, "active_orator_id": 99}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 5.0}
    session = MagicMock(status="active")
    manager = MagicMock()
    manager.get_or_create_session.return_value = session
    req = MagicMock()

    # Owner (11) is rejected because active_orator_id is 99
    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 11})),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app_module.start_agent_endpoint("stage", req))
    assert exc_info.value.status_code == 403

    # Active orator (99) is accepted
    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 99})),
    ):
        result = asyncio.run(app_module.start_agent_endpoint("stage", req))
    assert result["agent_running"] is True


@pytest.mark.parametrize("endpoint", ["start", "status"])
@pytest.mark.parametrize(
    ("owner_credits", "orator_credits"), [(5.0, 0.0), (5.0, 100.0), (0.0, 100.0), (-1.0, 100.0)],
)
def test_agent_credit_checks_use_owner_after_baton_handoff(
    endpoint: str, owner_credits: float, orator_credits: float,
) -> None:
    accounts = {
        11: {"id": 11, "credits": owner_credits},
        99: {"id": 99, "credits": orator_credits},
    }
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11, "active_orator_id": 99}
    registry_db.get_user_by_id.side_effect = accounts.__getitem__
    manager = MagicMock()
    session = SimpleNamespace(
        status="active", websocket_connected=True, created_at="now", last_active_at="later",
        summon=MagicMock(),
    )
    manager.get_session.return_value = session
    manager.get_or_create_session.return_value = session
    client = TestClient(app_module.app)

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value=accounts[99])),
    ):
        url = f"/api/theaters/stage/agent/{endpoint}"
        response = client.post(url) if endpoint == "start" else client.get(url)

    assert response.status_code == (402 if endpoint == "start" and owner_credits <= 0 else 200)
    assert response.json()["credits"] == owner_credits
    assert response.json()["insufficient_credits"] == (owner_credits <= 0)
    assert response.json()["agent_running"] == (owner_credits > 0)
    registry_db.get_user_by_id.assert_called_once_with(11)
    if owner_credits <= 0:
        if endpoint == "start":
            assert "theater owner's credit balance" in response.json()["detail"]
        manager.stop_session.assert_called_once_with(theater_id="stage")
        manager.get_or_create_session.assert_not_called()
    else:
        manager.stop_session.assert_not_called()
        if endpoint == "start":
            session.summon.assert_called_once_with()


@pytest.mark.parametrize(
    ("owner_credits", "orator_credits"), [(5.0, 0.0), (5.0, 100.0), (0.0, 100.0), (-1.0, 100.0)],
)
def test_agent_websocket_checks_owner_credits_after_baton_handoff(
    owner_credits: float, orator_credits: float,
) -> None:
    accounts = {
        11: {"id": 11, "credits": owner_credits},
        99: {"id": 99, "credits": orator_credits},
    }
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11, "active_orator_id": 99}
    registry_db.get_user_by_id.side_effect = accounts.__getitem__
    manager = MagicMock()
    websocket = MagicMock(spec=WebSocket)
    handler = AsyncMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value=accounts[99])),
        patch.object(app_module, "handle_live_websocket_connection", handler),
    ):
        asyncio.run(app_module.agent_websocket_endpoint(websocket, "stage"))

    registry_db.get_user_by_id.assert_called_once_with(11)
    if owner_credits <= 0:
        manager.stop_session.assert_called_once_with(theater_id="stage")
        handler.assert_not_awaited()
        websocket.accept.assert_awaited_once_with()
        message = websocket.send_json.call_args.args[0]
        assert message["type"] == "insufficient_credits"
        assert message["credits"] == owner_credits
        websocket.close.assert_awaited_once_with(code=1008)
    else:
        manager.stop_session.assert_not_called()
        websocket.close.assert_not_awaited()
        handler.assert_awaited_once_with(
            websocket=websocket, theater_id="stage", live_agent_manager=app_module.live_agent_manager,
            user_id=99, canvas_state_service=app_module.canvas_states,
        )


def test_stop_agent_rejects_unauthenticated_request() -> None:
    req = MagicMock()
    with patch.object(app_module, "get_current_user_async", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app_module.stop_agent_endpoint("stage", req))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required."


def test_stop_agent_rejects_non_orator() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11, "active_orator_id": 11}
    req = MagicMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 99})),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(app_module.stop_agent_endpoint("stage", req))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Only the active orator can stop the agent."


def test_stop_agent_allows_active_orator() -> None:
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    manager = MagicMock()
    manager.stop_session.return_value = True
    req = MagicMock()

    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(object_registry, "live_agent_manager", manager),
        patch.object(app_module, "get_current_user_async", AsyncMock(return_value={"id": 11})),
    ):
        result = asyncio.run(app_module.stop_agent_endpoint("stage", req))
    assert result["status"] == "stopped"
    assert result["agent_running"] is False
    manager.stop_session.assert_called_once_with(theater_id="stage")


def test_agent_status_reads_active_session_from_registry_manager():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 3.5}
    manager = MagicMock()
    manager.get_session.return_value = SimpleNamespace(
        status="running", websocket_connected=True, created_at="now", last_active_at="later"
    )

    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "live_agent_manager", manager):
        result = asyncio.run(app_module.get_agent_status_endpoint("stage"))

    assert result["agent_running"] is True
    assert result["websocket_connected"] is True
    assert result["credits"] == 3.5


def test_server_shutdown_closes_database_connection():
    mock_db = MagicMock()
    with patch.object(object_registry, "db", mock_db):
        object_registry.shutdown_database_connection()
    mock_db.close.assert_called_once()


def test_lifespan_preloads_adventure_cards_before_serving() -> None:
    service = MagicMock(spec=object_registry.AdventureService)
    database = MagicMock()

    async def run_lifespan() -> None:
        async with object_registry.lifespan(object_registry.app):
            service.list_adventures.assert_called_once_with(force_refresh=True)
            database.close.assert_not_called()

    with (
        patch.object(object_registry, "adventure_service", service),
        patch.object(object_registry, "db", database),
        patch("api_server.pages.rebuild_docs_search_index"),
    ):
        asyncio.run(run_lifespan())
    database.close.assert_called_once()


def test_adventure_monitor_retries_after_storage_failure() -> None:
    service = MagicMock(spec=object_registry.AdventureService)

    async def run_monitor() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def refresh() -> bool:
            if service.refresh_if_changed.call_count == 1:
                raise OSError("Temporary storage failure")
            loop.call_soon_threadsafe(stop.set)
            return False

        service.refresh_if_changed.side_effect = refresh
        await asyncio.wait_for(object_registry.monitor_adventure_catalog(stop), timeout=5)

    with (
        patch.object(object_registry, "adventure_service", service),
        patch.object(object_registry, "CACHE_CHECK_INTERVAL_SECONDS", 0.001),
    ):
        asyncio.run(run_monitor())
    assert service.refresh_if_changed.call_count == 2


def test_generic_websocket_fallback_accepts_connection():
    from fastapi.testclient import TestClient
    client = TestClient(app_module.app)
    with client.websocket_connect("/ws?clientId=fb83c52d027d457ab5c535e0067c2540") as websocket:
        assert websocket is not None


def test_log_filter_truncates_large_request_payload_in_logger_debug():
    import logging
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)

    log_filter = app_module.LogFilter(prefixes="[Story", max_payload_len=80)
    handler.addFilter(log_filter)

    flow_logger = logging.getLogger("google_adk.google.adk.flows.llm_flows.base_llm_flow")
    flow_logger.setLevel(logging.DEBUG)
    flow_logger.addHandler(handler)

    huge_content = "Content(parts=[Part(text='[Story] " + "Z" * 1000 + "')])"
    flow_logger.debug("Sending live request %s to active streams: %s", huge_content, ["stream_1"])

    output = buf.getvalue().strip()
    assert "... [truncated]" in output
    assert len(output) < 250
    assert "Z" * 1000 not in output


def test_log_filter_truncates_gemini_llm_connection_debug():
    import logging
    import io

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    log_filter = app_module.LogFilter(prefixes="[Story", max_payload_len=80)
    handler.addFilter(log_filter)

    conn_logger = logging.getLogger("google_adk.google.adk.models.gemini_llm_connection")
    conn_logger.setLevel(logging.DEBUG)
    conn_logger.addHandler(handler)

    huge_parts = "parts=[Part(text='[Story] " + "Y" * 1000 + "')]"
    conn_logger.debug("Sending LLM new content %s", huge_parts)

    output = buf.getvalue().strip()
    assert "... [truncated]" in output
    assert len(output) < 250
    assert "Y" * 1000 not in output


def test_suppress_noisy_loggers_sets_wire_loggers_to_info():
    import logging
    app_module.suppress_noisy_loggers("[Story")
    for noisy in ("PIL", "httpcore", "httpx", "websockets", "urllib3"):
        assert logging.getLogger(noisy).level == logging.INFO


def test_game_package_loads_without_cel_tracing_under_app_debug_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from game.game_runtime import validate_game_package
    from testing.game_fixtures import counter_definition

    def reject_cel_trace(record: logging.LogRecord) -> bool:
        assert not (record.name.startswith("celpy.") and record.levelno == logging.DEBUG), (
            "CEL interpreter tracing must stay disabled for application debug prefixes"
        )
        return True

    cel_logger = logging.getLogger("celpy")
    previous_level = cel_logger.level
    caplog.handler.addFilter(reject_cel_trace)
    try:
        cel_logger.setLevel(logging.NOTSET)
        with caplog.at_level(logging.DEBUG):
            app_module.suppress_noisy_loggers("[GameState]")
            program = validate_game_package(counter_definition().model_dump_json().encode("utf-8"))
            assert program.initial_view
            assert program.definition.initial == {"count": 0}
    finally:
        caplog.handler.removeFilter(reject_cel_trace)
        cel_logger.setLevel(previous_level)


def test_cel_tracing_can_be_explicitly_requested(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    cel_logger = logging.getLogger("celpy")
    previous_level = cel_logger.level
    try:
        cel_logger.setLevel(logging.NOTSET)
        with caplog.at_level(logging.DEBUG):
            app_module.suppress_noisy_loggers("celpy")
            logging.getLogger("celpy.Evaluator").debug("CEL trace probe")
        assert "CEL trace probe" in caplog.messages
    finally:
        cel_logger.setLevel(previous_level)


if __name__ == "__main__":
    unittest.main()

