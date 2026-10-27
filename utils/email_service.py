import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Literal, TypedDict

from absl import flags
from dotenv import load_dotenv

from utils.config_loader import get_app_config

load_dotenv()

logger = logging.getLogger(__name__)

# Define abseil flag for controlling actual email dispatch
if "send_emails" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "send_emails",
        True,
        "Whether to actually send emails via SMTP.",
    )

FLAGS = flags.FLAGS


class EmailSendResult(TypedDict):
    sent: bool
    method: Literal["smtp", "simulated", "failed"]


def send_password_reset_email(to_email: str, username: str, reset_link: str) -> EmailSendResult:
    """Send a reset email, reporting failures without logging recovery credentials."""
    try:
        should_send = bool(FLAGS.send_emails)
    except flags.UnparsedFlagAccessError:
        logger.error("Password reset email failed: send_emails flag has not been parsed.")
        return {"sent": False, "method": "failed"}

    if not should_send:
        logger.info("Password reset email simulated: --send_emails is disabled; no email sent.")
        return {"sent": False, "method": "simulated"}

    config = get_app_config()
    smtp_cfg = config.get("smtp", {})

    smtp_host = smtp_cfg.get("host", "")
    try:
        smtp_port = int(smtp_cfg.get("port", 587))
        if not 1 <= smtp_port <= 65535:
            raise ValueError("SMTP port is outside the valid range")
    except (TypeError, ValueError):
        logger.error("Password reset email failed: invalid SMTP port configuration.")
        return {"sent": False, "method": "failed"}
    smtp_user = os.getenv("SMTP_USERNAME") or os.getenv("STMP_USERNAME") or ""
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = smtp_cfg.get("from_email", "noreply@narratron.io")

    missing = [
        name for name, value in (
            ("host", smtp_host),
            ("SMTP_USERNAME", smtp_user),
            ("SMTP_PASSWORD", smtp_password),
            ("from_email", smtp_from),
        ) if not value
    ]
    if missing:
        logger.error("Password reset email failed: missing SMTP configuration: %s.", ", ".join(missing))
        return {"sent": False, "method": "failed"}

    subject = "Narratron Buddy - Password Reset Request"
    text_content = (
        f"Hello {username},\n\n"
        f"We received a request to reset your password for your Narratron Buddy account.\n"
        f"Click the following link or copy it into your browser to reset your password:\n\n"
        f"{reset_link}\n\n"
        f"This link will expire in 30 minutes.\n"
        f"If you did not request this, please ignore this email.\n"
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
            .container {{ max-width: 500px; margin: 0 auto; background-color: #1e293b; border-radius: 12px; padding: 30px; border: 1px solid #334155; }}
            .header {{ text-align: center; font-size: 24px; font-weight: bold; margin-bottom: 20px; color: #38bdf8; }}
            .btn {{ display: block; width: 200px; margin: 25px auto; padding: 12px 20px; background-color: #0284c7; color: #ffffff !important; text-align: center; font-weight: bold; text-decoration: none; border-radius: 8px; }}
            .footer {{ margin-top: 30px; font-size: 12px; text-align: center; color: #94a3b8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">🎭 Narratron Buddy</div>
            <p>Hello <strong>{username}</strong>,</p>
            <p>We received a request to reset your password for your account.</p>
            <a href="{reset_link}" class="btn">Reset Password</a>
            <p style="font-size: 13px; color: #94a3b8; word-break: break-all;">Or copy and paste this URL into your browser:<br>{reset_link}</p>
            <div class="footer">
                This link will expire in 30 minutes.<br>
                If you did not request a password reset, you can safely ignore this email.
            </div>
        </div>
    </body>
    </html>
    """

    stage = "message construction"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_from
        msg["To"] = to_email

        msg.attach(MIMEText(text_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        stage = "connection"
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            stage = "STARTTLS"
            server.starttls(context=ssl.create_default_context())
            stage = "authentication"
            server.login(smtp_user, smtp_password)
            stage = "message submission"
            refused = server.sendmail(smtp_from, [to_email], msg.as_string())
            if refused:
                logger.error("Password reset email failed during message submission: recipient refused.")
                return {"sent": False, "method": "failed"}
            stage = "connection close"

    except smtplib.SMTPResponseException as error:
        # SMTP response text can echo credentials or message content. Log only
        # the numeric status and exception class, without a traceback.
        logger.error(
            "Password reset email failed during %s: %s (SMTP status %d).",
            stage, type(error).__name__, error.smtp_code,
        )
        return {"sent": False, "method": "failed"}
    except (smtplib.SMTPException, OSError, ValueError) as error:
        logger.error("Password reset email failed during %s: %s.", stage, type(error).__name__)
        return {"sent": False, "method": "failed"}

    logger.info("Password reset email accepted by SMTP server.")
    return {"sent": True, "method": "smtp"}
