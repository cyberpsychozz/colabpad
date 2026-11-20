"""Outbound mail through an authenticated TLS SMTP connection, and the sign-in letter."""

import asyncio
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Protocol
from urllib.parse import urlsplit

from app.config import Settings
from app.i18n import translate

SENDER_NAME = "CollabPad"


class Mailer(Protocol):
    async def send(self, recipient: str, subject: str, body: str, html: str | None = None) -> None: ...


logger = logging.getLogger(__name__)


def login_mail(locale: str, code: str, minutes: int, site: str) -> tuple[str, str, str]:
    """Subject, plain text and HTML of the sign-in letter: a greeting, the code, what to do with
    it and what to do if you didn't ask for it. Plain wording and a real HTML part keep it out
    of spam folders better than a bare link did."""
    params = {"code": code, "minutes": minutes}

    def say(key: str) -> str:
        return translate(locale, key, params)

    site = site.rstrip("/")
    host = urlsplit(site).netloc or site
    subject = say("mail.login_subject")
    text = "\n\n".join([
        say("mail.login_greeting"),
        f"{say('mail.login_intro')} {code}",
        say("mail.login_how"),
        say("mail.login_ignore"),
        f"{say('mail.signature')}\n{site}",
    ]) + "\n"
    e = html.escape
    body = f"""<!doctype html>
<html lang="{e(locale)}"><head><meta charset="utf-8"><title>{e(subject)}</title></head>
<body style="margin:0;padding:24px 12px;background:#f4f1ea;color:#1f1d1a;font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;">
<div style="max-width:480px;margin:0 auto;background:#ffffff;border-radius:12px;padding:28px 24px;">
<p style="margin:0 0 16px;font-size:16px;line-height:1.5;">{e(say('mail.login_greeting'))}</p>
<p style="margin:0 0 8px;font-size:16px;line-height:1.5;">{e(say('mail.login_intro'))}</p>
<p style="margin:0 0 20px;font-size:32px;font-weight:700;letter-spacing:6px;font-family:Menlo,Consolas,monospace;">{e(code)}</p>
<p style="margin:0 0 16px;font-size:15px;line-height:1.5;">{e(say('mail.login_how'))}</p>
<p style="margin:0 0 24px;font-size:14px;line-height:1.5;color:#6b665e;">{e(say('mail.login_ignore'))}</p>
<p style="margin:0;font-size:13px;line-height:1.5;color:#8a857c;">{e(say('mail.signature'))}<br>
<a href="{e(site)}" style="color:#8a857c;">{e(host)}</a></p>
</div>
</body></html>
"""
    return subject, text, body


class ConsoleMailer:
    """No SMTP configured (local development): the mail goes to the server log instead."""

    async def send(self, recipient: str, subject: str, body: str, html: str | None = None) -> None:
        logger.warning("Mail to %s (SMTP_HOST is not set, nothing was sent)\n%s\n%s", recipient, subject, body)


def build_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else ConsoleMailer()


def compose(sender: str, recipient: str, subject: str, body: str, html: str | None) -> EmailMessage:
    """A letter with the headers spam filters look for: a named sender, Date and Message-ID."""
    name, address = parseaddr(sender)
    message = EmailMessage()
    message["From"] = formataddr((name or SENDER_NAME, address))
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=address.rpartition("@")[2] or None)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, recipient: str, subject: str, body: str, html: str | None = None) -> None:
        await asyncio.to_thread(self._send, recipient, subject, body, html)

    def _send(self, recipient: str, subject: str, body: str, html: str | None) -> None:
        settings = self.settings
        if not settings.smtp_host or not settings.smtp_from:
            raise OSError("SMTP delivery is not configured")
        message = compose(settings.smtp_from, recipient, subject, body, html)
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10,
                              context=ssl.create_default_context()) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
