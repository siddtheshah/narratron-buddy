import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from api_server.app import app, get_theater_owner_credits
from storage.database import DatabaseManager


class TestAgentCreditsEnforcement(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("object_registry.db")
    def test_get_theater_owner_credits_positive(self, mock_db):
        mock_db.get_deployment.return_value = {"theater_id": "theater_1", "user_id": 42}
        mock_db.get_user_by_id.return_value = {"id": 42, "credits": 50.0}

        has_credits, balance, owner_id = get_theater_owner_credits("theater_1")
        self.assertTrue(has_credits)
        self.assertEqual(balance, 50.0)
        self.assertEqual(owner_id, 42)

    @patch("object_registry.db")
    def test_get_theater_owner_credits_zero_or_negative(self, mock_db):
        mock_db.get_deployment.return_value = {"theater_id": "theater_1", "user_id": 42}
        mock_db.get_user_by_id.return_value = {"id": 42, "credits": 0.0}

        has_credits, balance, owner_id = get_theater_owner_credits("theater_1")
        self.assertFalse(has_credits)
        self.assertEqual(balance, 0.0)
        self.assertEqual(owner_id, 42)

        mock_db.get_user_by_id.return_value = {"id": 42, "credits": -5.0}
        has_credits_neg, balance_neg, owner_id_neg = get_theater_owner_credits("theater_1")
        self.assertFalse(has_credits_neg)
        self.assertEqual(balance_neg, -5.0)

    @patch("object_registry.db")
    def test_get_theater_owner_credits_missing_records(self, mock_db):
        mock_db.get_deployment.return_value = None
        has_credits, balance, owner_id = get_theater_owner_credits("non_existent_theater")
        self.assertFalse(has_credits)
        self.assertEqual(balance, 0.0)
        self.assertIsNone(owner_id)

    @patch("object_registry.db")
    @patch("object_registry.agent_manager")
    def test_start_agent_blocked_when_credits_le_zero(self, mock_agent_mgr, mock_db):
        mock_db.get_deployment.return_value = {"theater_id": "theater_poor", "user_id": 99}
        mock_db.get_user_by_id.return_value = {"id": 99, "credits": 0.0}

        response = self.client.post("/api/theaters/theater_poor/agent/start")
        self.assertEqual(response.status_code, 402)
        json_data = response.json()
        self.assertTrue(json_data.get("insufficient_credits"))
        self.assertEqual(json_data.get("agent_running"), False)
        mock_agent_mgr.stop_session.assert_called_once_with(theater_id="theater_poor")

    @patch("object_registry.db")
    @patch("object_registry.agent_manager")
    def test_start_agent_allowed_when_credits_positive(self, mock_agent_mgr, mock_db):
        mock_db.get_deployment.return_value = {"theater_id": "theater_rich", "user_id": 88}
        mock_db.get_user_by_id.return_value = {"id": 88, "credits": 20.0}
        mock_session = MagicMock()
        mock_session.status = "ready"
        mock_agent_mgr.get_or_create_session.return_value = mock_session

        response = self.client.post("/api/theaters/theater_rich/agent/start")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertFalse(json_data.get("insufficient_credits"))
        self.assertTrue(json_data.get("agent_running"))

    @patch("object_registry.db")
    @patch("object_registry.agent_manager")
    def test_get_agent_status_stops_session_when_credits_le_zero(self, mock_agent_mgr, mock_db):
        mock_db.get_deployment.return_value = {"theater_id": "theater_depleted", "user_id": 77}
        mock_db.get_user_by_id.return_value = {"id": 77, "credits": -1.0}
        mock_session = MagicMock()
        mock_session.status = "active"
        mock_agent_mgr.get_session.return_value = mock_session

        response = self.client.get("/api/theaters/theater_depleted/agent/status")
        self.assertEqual(response.status_code, 200)
        json_data = response.json()
        self.assertTrue(json_data.get("insufficient_credits"))
        self.assertFalse(json_data.get("agent_running"))
        mock_agent_mgr.stop_session.assert_called_once_with(theater_id="theater_depleted")

    @patch("services.agent_manager.logger")
    @patch("services.agent_manager.AgentSession._get_database")
    def test_flush_usage_stops_session_on_credit_exhaustion(self, mock_get_database, mock_logger):
        from services.agent_manager import AgentSession

        mock_runner = MagicMock()
        mock_session_service = MagicMock()
        mock_artifact_service = MagicMock()
        mock_agent = MagicMock()
        mock_db = MagicMock()
        mock_runner.agent = mock_agent
        mock_runner.session_service = mock_session_service
        mock_get_database.return_value = mock_db
        mock_db.get_deployment.return_value = {"user_id": 10}

        session = AgentSession(
            theater_id="t_exhaust",
            runner=mock_runner,
            tool_bundle=MagicMock(),
        )
        session.unbilled_audio_bytes = 1920000  # 1 voice minute
        session.close = MagicMock()

        # Database record_user_usage returns credits <= 0
        mock_db.record_user_usage.return_value = {"id": 10, "credits": 0.0}

        session.flush_usage_to_db()

        kwargs = mock_db.record_user_usage.call_args.kwargs
        self.assertEqual(kwargs["user_id"], 10)
        self.assertEqual(kwargs["voice_minutes"], 1.0)
        self.assertEqual(kwargs["images_created"], 0)
        self.assertTrue(kwargs["idempotency_key"].startswith("live-usage:t_exhaust:"))
        # Verify close was invoked due to 0 remaining credits
        session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
