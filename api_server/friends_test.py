"""Exercise friend lifecycle and theater authorization against isolated storage."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from api_server import friends
from storage.database import LocalDatabaseManager


@pytest.fixture
def database(tmp_path: Path) -> Iterator[LocalDatabaseManager]:
    database = LocalDatabaseManager(str(tmp_path / "friends.db"))
    database.register_user("Alice", "alice@example.com", "secret123")
    database.register_user("Bobby", "bob@example.com", "secret123")
    database.register_user("Carol", "carol@example.com", "secret123")
    yield database
    database.close()


def user_id(database: LocalDatabaseManager, username: str) -> int:
    user = database.get_user_by_username(username)
    assert user is not None
    return int(user["id"])


def test_lifecycle_and_theater_discovery(database: LocalDatabaseManager) -> None:
    alice = user_id(database, "Alice")
    bob = user_id(database, "Bobby")
    database.record_deployment("bob-theater", bob, "SECRET", name="Bob's story")
    assert database.get_friend_theaters(alice) == []
    database.friend_action(alice, "bobby", "request")
    database.friend_action(alice, "Bobby", "request")
    assert database.get_friendships(alice) == [{"username": "Bobby", "status": "outgoing"}]
    assert database.get_friendships(bob) == [{"username": "Alice", "status": "incoming"}]
    assert database.get_friend_theaters(alice) == []
    with pytest.raises(ValueError, match="incoming"):
        database.friend_action(alice, "Bobby", "accept")
    database.friend_action(bob, "Alice", "request")
    assert database.get_friendships(bob)[0]["status"] == "incoming"
    database.friend_action(bob, "Alice", "accept")
    assert database.get_friendships(alice)[0]["status"] == "accepted"
    assert database.get_friendships(bob)[0]["status"] == "accepted"
    assert database.get_friend_theaters(alice)[0]["theater_id"] == "bob-theater"
    assert database.get_friend_theaters(user_id(database, "Carol")) == []
    database.friend_action(bob, "Alice", "remove")
    assert database.get_friendships(alice) == database.get_friendships(bob) == []
    assert database.get_friend_theaters(alice) == []


def test_invalid_requests_and_account_deletion(database: LocalDatabaseManager) -> None:
    alice = user_id(database, "Alice")
    bob = user_id(database, "Bobby")
    for username in ("Alice", "missing"):
        with pytest.raises(ValueError):
            database.friend_action(alice, username, "request")
    database.friend_action(alice, "Bobby", "request")
    database.friend_action(bob, "Alice", "remove")
    assert database.get_friendships(alice) == []
    database.friend_action(alice, "Bobby", "request")
    database.friend_action(alice, "Bobby", "remove")
    assert database.get_friendships(bob) == []
    database.friend_action(alice, "Bobby", "request")
    database.delete_user(bob)
    assert database.get_friendships(alice) == []


def test_api_admission_rechecks_friendship_and_hides_keys(database: LocalDatabaseManager) -> None:
    alice = user_id(database, "Alice")
    bob = user_id(database, "Bobby")
    database.record_deployment("bob-theater", bob, "SECRET", name="Bob's story")
    manager = MagicMock()
    manager.get_session.return_value = None
    with patch.object(friends, "db", database), patch.object(friends, "live_agent_manager", manager), \
         patch.object(friends, "get_current_user", return_value={"id": alice}), \
         patch.object(friends, "_grant_canvas_access") as grant, TestClient(friends.app) as client:
        assert client.post('/api/friend-theaters/bob-theater/join').status_code == 404
        assert client.post('/api/friends/Bobby', json={"action": "request"}).status_code == 200
        assert client.post('/api/friends/Bobby', json={"action": "accept"}).status_code == 400
        assert client.get('/api/friend-theaters').json() == []
        database.friend_action(bob, "Alice", "accept")
        response = client.get('/api/friend-theaters')
        assert response.headers['cache-control'] == 'no-store'
        assert response.json() == [{"theater_id": "bob-theater", "name": "Bob's story", "username": "Bobby", "online": False}]
        manager.get_session.return_value = MagicMock(is_online=True)
        assert client.get('/api/friend-theaters').json()[0]['online'] is True
        assert client.post('/api/friend-theaters/bob-theater/join').json() == {"theater_id": "bob-theater"}
        assert grant.call_args.args[2:] == ("bob-theater", "SECRET")
        database.friend_action(bob, "Alice", "remove")
        assert client.post('/api/friend-theaters/bob-theater/join').status_code == 404
        assert grant.call_count == 1


def test_api_requires_login() -> None:
    with patch.object(friends, "get_current_user", return_value=None), TestClient(friends.app) as client:
        assert client.get('/api/friends').status_code == 401
        assert client.get('/api/friend-theaters').status_code == 401
        assert client.post('/api/friends/Alice', json={"action": "request"}).status_code == 401
        assert client.post('/api/friend-theaters/anything/join').status_code == 401
