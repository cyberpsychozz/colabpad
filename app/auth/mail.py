"""Outbound mail through an authenticated TLS SMTP connection."""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import Settings


class Mailer(Protocol):
    async def send(self, recipient: str, subject: str, body: str) -> None: ...


logger = logging.getLogger(__name__)


class ConsoleMailer:
    """No SMTP configured (local development): the mail goes to the server log instead."""

    async def send(self, recipient: str, subject: str, body: str) -> None:
        logger.warning("Mail to %s (SMTP_HOST is not set, nothing was sent)\n%s\n%s", recipient, subject, body)


def build_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else ConsoleMailer()


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, recipient: str, subject: str, body: str) -> None:
        await asyncio.to_thread(self._send, recipient, subject, body)

    def _send(self, recipient: str, subject: str, body: str) -> None:
        settings = self.settings
        if not settings.smtp_host or not settings.smtp_from:
            raise OSError("SMTP delivery is not configured")
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10,
                              context=ssl.create_default_context()) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
