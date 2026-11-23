import logging
import smtplib
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("lumen.email")


# Without SMTP, sent emails are kept here too (tests and development read them)
OUTBOX: list[EmailMessage] = []


def send_email(
    to: str,
    subject: str,
    text: str,
    html: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    """Send an email (plain text, optional HTML and attachments as (filename, bytes, mime type)).
    Without SMTP configured, the email is written to the log. SMTP comes from Admin → Settings → Email, or
    the SMTP_* environment settings."""
    from app.services.org import smtp as smtp_settings

    s = smtp_settings()
    msg = EmailMessage()
    msg["From"] = s["sender"] or get_settings().smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data, mime in attachments or []:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream", filename=name)
    if not s["host"]:
        log.warning("SMTP not configured; email to %s\nSubject: %s\n\n%s", to, subject, text)
        OUTBOX.append(msg)
        del OUTBOX[:-50]
        return
    with smtplib.SMTP(s["host"], s["port"], timeout=20) as smtp:
        if s["tls"]:
            smtp.starttls()
        if s["user"]:
            smtp.login(s["user"], s["password"])
        smtp.send_message(msg)
