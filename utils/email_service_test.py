"""Offline coverage for SMTP delivery, explicit simulation, and safe diagnostics."""

from collections.abc import Iterator
import logging
import smtplib
from unittest.mock import MagicMock, patch

from absl import flags
from absl.testing import flagsaver
import pytest

from utils import email_service


RESET_LINK = "https://example.test/deploy?reset_token=private-reset-token"
RECIPIENT = "user@example.test"
SMTP_PASSWORD = "private-smtp-password"


@pytest.fixture
def smtp_mock(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    monkeypatch.setenv("SMTP_USERNAME", "smtp-user@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", SMTP_PASSWORD)
    server = MagicMock(spec=smtplib.SMTP)
    server.sendmail.return_value = {}
    with (
        flagsaver.flagsaver(send_emails=True),
        patch.object(email_service, "get_app_config", return_value={
            "smtp": {"host": "smtp.example.test", "port": 587, "from_email": "noreply@example.test"},
        }),
        patch.object(email_service.smtplib, "SMTP", autospec=True) as constructor,
    ):
        constructor.return_value.__enter__.return_value = server
        yield constructor


def test_success_means_smtp_accepted_the_message(smtp_mock: MagicMock) -> None:
    result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": True, "method": "smtp"}
    smtp_mock.assert_called_once_with("smtp.example.test", 587, timeout=10)
    server = smtp_mock.return_value.__enter__.return_value
    assert server.starttls.call_args.kwargs["context"].check_hostname is True
    server.login.assert_called_once_with("smtp-user@example.test", SMTP_PASSWORD)
    sender, recipients, message = server.sendmail.call_args.args
    assert sender == "noreply@example.test"
    assert recipients == [RECIPIENT]
    assert RESET_LINK in message


def test_disabled_sending_is_not_reported_as_delivery(
    smtp_mock: MagicMock, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    with flagsaver.flagsaver(send_emails=False), caplog.at_level(logging.INFO):
        result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "simulated"}
    smtp_mock.assert_not_called()
    assert "no email sent" in caplog.text
    assert "private-reset-token" not in caplog.text
    assert capsys.readouterr().out == ""


def test_unparsed_flags_fail_instead_of_silently_simulating(smtp_mock: MagicMock) -> None:
    unparsed_flags = flags.FlagValues()
    flags.DEFINE_boolean("send_emails", True, "Send email", flag_values=unparsed_flags)
    with patch.object(email_service, "FLAGS", unparsed_flags):
        result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "failed"}
    smtp_mock.assert_not_called()


@pytest.mark.parametrize("missing_key", ["host", "from_email", "SMTP_USERNAME", "SMTP_PASSWORD"])
def test_missing_configuration_is_an_error(
    smtp_mock: MagicMock, monkeypatch: pytest.MonkeyPatch, missing_key: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = {"host": "smtp.example.test", "port": 587, "from_email": "noreply@example.test"}
    if missing_key in {"SMTP_USERNAME", "SMTP_PASSWORD"}:
        monkeypatch.delenv(missing_key)
        monkeypatch.delenv("STMP_USERNAME", raising=False)
    else:
        config[missing_key] = ""
    with patch.object(email_service, "get_app_config", return_value={"smtp": config}):
        result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "failed"}
    smtp_mock.assert_not_called()
    assert missing_key in caplog.text
    assert SMTP_PASSWORD not in caplog.text
    assert "private-reset-token" not in caplog.text


@pytest.mark.parametrize("port", ["invalid", None, 0, 65536])
def test_invalid_port_is_an_error(smtp_mock: MagicMock, port: str | int | None) -> None:
    with patch.object(email_service, "get_app_config", return_value={"smtp": {"port": port}}):
        result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "failed"}
    smtp_mock.assert_not_called()


@pytest.mark.parametrize("stage", ["connection", "STARTTLS", "authentication", "message submission"])
def test_smtp_response_errors_include_stage_and_code_without_secrets(
    smtp_mock: MagicMock, stage: str, caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = smtp_mock.return_value.__enter__.return_value
    operations = {
        "connection": smtp_mock,
        "STARTTLS": server.starttls,
        "authentication": server.login,
        "message submission": server.sendmail,
    }
    operations[stage].side_effect = smtplib.SMTPResponseException(
        535, f"{RESET_LINK} {SMTP_PASSWORD}".encode(),
    )

    result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "failed"}
    assert f"during {stage}" in caplog.text
    assert "SMTP status 535" in caplog.text
    assert "private-reset-token" not in caplog.text
    assert SMTP_PASSWORD not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("error", [TimeoutError("private-reset-token"), smtplib.SMTPNotSupportedError(SMTP_PASSWORD)])
def test_transport_errors_are_reported_without_sensitive_exception_text(
    smtp_mock: MagicMock, error: Exception, caplog: pytest.LogCaptureFixture,
) -> None:
    smtp_mock.side_effect = error

    result = email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)

    assert result == {"sent": False, "method": "failed"}
    assert type(error).__name__ in caplog.text
    assert "private-reset-token" not in caplog.text
    assert SMTP_PASSWORD not in caplog.text


def test_recipient_refusal_cannot_be_reported_as_sent(smtp_mock: MagicMock) -> None:
    smtp_mock.return_value.__enter__.return_value.sendmail.return_value = {
        RECIPIENT: (550, b"recipient refused"),
    }

    assert email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK) == {
        "sent": False, "method": "failed",
    }


def test_legacy_username_environment_variable_still_works(
    smtp_mock: MagicMock, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SMTP_USERNAME")
    monkeypatch.setenv("STMP_USERNAME", "legacy@example.test")

    assert email_service.send_password_reset_email(RECIPIENT, "testuser", RESET_LINK)["sent"]
    smtp_mock.return_value.__enter__.return_value.login.assert_called_once_with("legacy@example.test", SMTP_PASSWORD)
