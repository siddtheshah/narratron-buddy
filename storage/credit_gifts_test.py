"""Gift claims transfer credits and establish accepted friendships together."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest

from storage.database import LocalDatabaseManager


@pytest.fixture
def database(tmp_path: Path) -> Iterator[LocalDatabaseManager]:
    database = LocalDatabaseManager(str(tmp_path / "gifts.db"))
    database.register_user("Alice", "alice@example.com", "secret123")
    database.register_user("Bobby", "bob@example.com", "secret123")
    yield database
    database.close()


def user_id(database: LocalDatabaseManager, username: str) -> int:
    user = database.get_user_by_username(username)
    assert user is not None
    return int(user["id"])


@pytest.mark.parametrize("sender_name", ["Alice", "Bobby"])
@pytest.mark.parametrize("relationship", ["none", "outgoing", "incoming", "accepted"])
def test_claim_creates_or_accepts_friendship(
    database: LocalDatabaseManager,
    sender_name: str,
    relationship: Literal["none", "outgoing", "incoming", "accepted"],
) -> None:
    recipient_name = "Bobby" if sender_name == "Alice" else "Alice"
    sender = user_id(database, sender_name)
    recipient = user_id(database, recipient_name)
    database.add_user_credits(sender, 20.0, 1.0)
    database.record_deployment("sender-theater", sender, "SECRET")
    if relationship in ("outgoing", "accepted"):
        database.friend_action(sender, recipient_name, "request")
    elif relationship == "incoming":
        database.friend_action(recipient, sender_name, "request")
    if relationship == "accepted":
        database.friend_action(recipient, sender_name, "accept")

    gift = database.create_credit_gift(sender, 5.0)
    if relationship == "none":
        assert database.get_friendships(sender) == database.get_friendships(recipient) == []
    result = database.claim_credit_gift(gift["token"], recipient)

    assert result["credits"] == 5.0
    assert database.get_user_by_id(sender)["credits"] == 15.0
    assert database.get_user_by_id(recipient)["credits"] == 5.0
    assert database.get_credit_gift(gift["token"])["status"] == "claimed"
    assert database.get_friendships(sender) == [{"username": recipient_name, "status": "accepted"}]
    assert database.get_friendships(recipient) == [{"username": sender_name, "status": "accepted"}]
    assert database.get_friend_theaters(recipient)[0]["theater_id"] == "sender-theater"

    # Further gifts preserve the single accepted relationship.
    another_gift = database.create_credit_gift(sender, 1.0)
    database.claim_credit_gift(another_gift["token"], recipient)
    assert database.get_friendships(sender) == [{"username": recipient_name, "status": "accepted"}]


@pytest.mark.parametrize(
    ("failure", "message"),
    [("missing", "not found"), ("self", "own gift"), ("expired", "expired"),
     ("insufficient", "enough credits")],
)
def test_rejected_claim_does_not_create_friendship(
    database: LocalDatabaseManager,
    failure: Literal["missing", "self", "expired", "insufficient"],
    message: str,
) -> None:
    sender = user_id(database, "Alice")
    recipient = user_id(database, "Bobby")
    database.add_user_credits(sender, 20.0, 1.0)
    gift = database.create_credit_gift(sender, 25.0 if failure == "insufficient" else 5.0)
    token = gift["token"]
    if failure == "expired":
        with database._get_connection() as conn:
            conn.cursor().execute(
                "UPDATE credit_referrals SET expires_at = ? WHERE token = ?",
                ("2000-01-01T00:00:00+00:00", token),
            )

    with pytest.raises(ValueError, match=message):
        database.claim_credit_gift(
            "missing-token" if failure == "missing" else token,
            sender if failure == "self" else recipient,
        )

    assert database.get_friendships(sender) == database.get_friendships(recipient) == []
    assert database.get_user_by_id(sender)["credits"] == 20.0
    assert database.get_user_by_id(recipient)["credits"] == 0.0
    assert database.get_credit_gift(token)["claimed"] is False


def test_replayed_claim_does_not_restore_removed_friendship(database: LocalDatabaseManager) -> None:
    sender = user_id(database, "Alice")
    recipient = user_id(database, "Bobby")
    database.add_user_credits(sender, 20.0, 1.0)
    gift = database.create_credit_gift(sender, 5.0)
    database.claim_credit_gift(gift["token"], recipient)
    database.friend_action(recipient, "Alice", "remove")

    with pytest.raises(ValueError, match="already been claimed"):
        database.claim_credit_gift(gift["token"], recipient)

    assert database.get_friendships(sender) == database.get_friendships(recipient) == []
    assert database.get_user_by_id(sender)["credits"] == 15.0
    assert database.get_user_by_id(recipient)["credits"] == 5.0
