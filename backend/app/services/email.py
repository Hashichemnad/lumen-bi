import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("lumen.email")


def send_email(to: str, subject: str, text: str) -> None:
    """Send a plain-text email. Without SMTP configured, the email is written to the log."""
    s = get_settings()
    if not s.smtp_host:
        log.warning("SMTP not configured; email to %s\nSubject: %s\n\n%s", to, subject, text)
        return
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
        if s.smtp_tls:
            smtp.starttls()
        if s.smtp_user:
            smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
