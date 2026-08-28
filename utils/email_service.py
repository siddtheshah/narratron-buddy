import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict

from absl import flags
from dotenv import load_dotenv

from utils.config_loader import get_app_config

load_dotenv()

logger = logging.getLogger(__name__)

# Define abseil flag for controlling actual email dispatch
flags.DEFINE_boolean(
    "send_emails",
    True,
    "Whether to actually send emails via SMTP.",
)

FLAGS = flags.FLAGS


def send_password_reset_email(to_email: str, username: str, reset_link: str) -> Dict[str, Any]:
    """Send a password reset email via SMTP if enabled via abseil flag, or log the reset link if disabled/unconfigured.

    Returns a dictionary containing execution status and debug details.
    """
    config = get_app_config()
    smtp_cfg = config.get("smtp", {})

    smtp_host = smtp_cfg.get("host", "")
    smtp_port = int(smtp_cfg.get("port", 587))
    smtp_user = os.getenv("SMTP_USERNAME") or os.getenv("STMP_USERNAME") or ""
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    smtp_from = smtp_cfg.get("from_email", "noreply@narratron.io")

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

    try:
        should_send = bool(FLAGS.send_emails)
    except Exception:
        should_send = False

    if should_send and smtp_host:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_from
            msg["To"] = to_email

            msg.attach(MIMEText(text_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                server.starttls()
                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, [to_email], msg.as_string())

            logger.info(f"Password reset email sent via SMTP to {to_email}")
            return {"sent": True, "method": "smtp", "reset_link": reset_link}
        except Exception as e:
            logger.error(f"Failed to send email via SMTP to {to_email}: {e}")

    # Fallback/Development mode: Log the link
    logger.info(f"[DEV/MOCK EMAIL] Password reset link for {username} ({to_email}): {reset_link}")
    print("\n==========================================")
    print(f"PASSWORD RESET LINK FOR {to_email}:")
    print(f"{reset_link}")
    print("==========================================\n")

    return {"sent": True, "method": "simulated", "reset_link": reset_link}
