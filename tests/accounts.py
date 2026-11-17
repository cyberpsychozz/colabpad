"""Helpers for account tests: a fake mailbox, signing in, and a settable clock."""

import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_asgi

DAY = 86_400


class FakeMailbox:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    async def send(self, email: str, subject: str, body: str) -> None:
        self.messages.append((email, subject, body))


def browser(application: FastAPI) -> TestClient:
    """Another browser on the same running app (the fixture client owns the lifespan)."""
    return TestClient(create_asgi(application))


def sign_in(application: FastAPI, client: TestClient, email: str) -> dict[str, Any]:
    if not isinstance(application.state.mailer, FakeMailbox):
        application.state.mailer = FakeMailbox()
    assert client.post("/api/auth/magic-link", json={"email": email}).status_code == 204
    url = urlsplit(re.search(r"https?://\S+", application.state.mailer.messages[-1][2])[0])
    assert client.get(f"{url.path}?{url.query}", follow_redirects=False).status_code == 302
    return client.get("/api/me").json()["user"]


def set_clock(application: FastAPI, now: dict[str, float]) -> None:
    application.state.billing.clock = lambda: now["t"]
    application.state.alive.clock = lambda: now["t"]


def give_plus(application: FastAPI, user_id: str, days: float = 30) -> None:
    billing = application.state.billing
    application.state.repo.activate_subscription(user_id, billing.now() + int(days * DAY * 1000), None)
