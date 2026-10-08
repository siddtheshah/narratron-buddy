"""Exercise abuse controls without model calls or waiting on wall-clock time."""

from unittest.mock import patch

import pytest

from services.user_help_limits import HelpRateLimitError, UserHelpLimits


def ask(limits: UserHelpLimits, principal: str, address: str, now: float) -> None:
    with patch("services.user_help_limits.time.monotonic", return_value=now):
        with limits.claim(principal, address):
            pass


def test_cooldown_and_hourly_user_limit_expire() -> None:
    limits = UserHelpLimits()
    ask(limits, "user:7", "ip:one", 0.0)
    with pytest.raises(HelpRateLimitError) as caught:
        ask(limits, "user:7", "ip:two", 3.0)
    assert caught.value.retry_after == 12
    for count in range(1, 20):
        ask(limits, "user:7", "ip:one", count * 15.0)
    with pytest.raises(HelpRateLimitError) as caught:
        ask(limits, "user:7", "ip:three", 300.0)
    assert caught.value.retry_after == 3300
    ask(limits, "user:7", "ip:one", 3600.0)


def test_changing_accounts_cannot_bypass_address_limit() -> None:
    limits = UserHelpLimits()
    for count in range(60):
        ask(limits, f"user:{count}", "ip:one", float(count))
    with pytest.raises(HelpRateLimitError):
        ask(limits, "user:new", "ip:one", 60.0)
    ask(limits, "user:new", "ip:two", 60.0)


def test_changing_accounts_and_addresses_cannot_bypass_total_budget() -> None:
    limits = UserHelpLimits()
    for count in range(300):
        ask(limits, f"user:{count}", f"ip:{count}", float(count))
    with pytest.raises(HelpRateLimitError):
        ask(limits, "user:new", "ip:new", 300.0)
    ask(limits, "user:new", "ip:new", 3600.0)


def test_concurrency_and_single_principal_guards_release_on_failure() -> None:
    limits = UserHelpLimits()
    with patch("services.user_help_limits.time.monotonic", return_value=0.0):
        with limits.claim("user:one", "ip:one"):
            with pytest.raises(HelpRateLimitError):
                ask(limits, "user:one", "ip:other", 30.0)
            with limits.claim("user:two", "ip:two"), limits.claim("user:three", "ip:three"):
                with pytest.raises(HelpRateLimitError):
                    ask(limits, "user:four", "ip:four", 30.0)
    with pytest.raises(RuntimeError):
        with limits.claim("user:four", "ip:four"):
            raise RuntimeError("research failed")
    assert not limits._active
    ask(limits, "user:five", "ip:five", 60.0)


def test_tracking_is_bounded_and_old_entries_are_removed() -> None:
    limits = UserHelpLimits()
    limits.max_tracked_keys = 2
    ask(limits, "user:one", "ip:one", 0.0)
    with pytest.raises(HelpRateLimitError):
        ask(limits, "user:two", "ip:two", 15.0)
    ask(limits, "user:two", "ip:two", 3600.0)
    assert set(limits._requests) == {"user:two", "ip:two"}


def test_anonymous_requests_are_counted_once() -> None:
    limits = UserHelpLimits()
    ask(limits, "ip:one", "ip:one", 0.0)
    assert len(limits._requests["ip:one"]) == 1
    assert len(limits._total_requests) == 1
