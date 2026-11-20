import asyncio
import email
import hashlib

from fastapi.testclient import TestClient

from app.auth.mail import compose
from app.i18n import translate
from tests.accounts import FakeMailbox, browser, mailed_code


def test_code_links_session_preserves_quota_and_rotates_cookie(application, client):
    mailbox = application.state.mailer = FakeMailbox()
    repo = application.state.repo
    anon = client.get("/api/me", headers={"Accept-Language": "ru"}).json()
    old_cookie = client.cookies["cp_session"]
    repo.spend_quota("s:" + old_cookie, "2026-01-01")
    assert client.post("/api/auth/code", json={"email": " Tester@Example.org "}).status_code == 204
    address, subject, body = mailbox.messages[0]
    code = mailed_code(application)
    assert address == "tester@example.org"
    assert subject == translate(anon["locale"], "mail.login_subject", {"code": code})
    assert "http" not in body.split(code)[0]  # no link to click, only the code
    assert code in mailbox.html[0] and "<html lang=\"ru\">" in mailbox.html[0]
    stored, = repo.db.execute("SELECT code_hash FROM login_code").fetchone()
    assert stored == hashlib.sha256(f"{old_cookie}:{code}".encode()).hexdigest() and code not in stored
    result = client.post("/api/auth/verify", json={"code": f"{code[:3]} {code[3:]}"})
    assert result.status_code == 204
    assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=lax" in result.headers["set-cookie"]
    credential = client.cookies["cp_session"]
    assert credential != old_cookie and repo.cookie_session(old_cookie) is None
    me = client.get("/api/me").json()
    assert me["user"]["email"] == address and me["quota"]["limit"] == 20
    assert repo.cookie_session(credential)["id"] == old_cookie
    assert repo.quota_used("u:" + me["user"]["id"], "2026-01-01") == 1
    # Single use: the same code does nothing the second time.
    assert client.post("/api/auth/verify", json={"code": code}).json()["error"]["code"] == "CODE_EXPIRED"
    assert client.post("/api/auth/logout").status_code == 204
    assert repo.cookie_session(credential) is None
    assert client.get("/api/me").json()["user"] is None


def test_a_code_works_only_in_the_browser_that_asked_for_it(application, client):
    application.state.mailer = FakeMailbox()
    assert client.post("/api/auth/code", json={"email": "a@example.org"}).status_code == 204
    code = mailed_code(application)
    other = browser(application)
    other.get("/api/me")
    assert other.post("/api/auth/verify", json={"code": code}).json()["error"]["code"] == "CODE_EXPIRED"
    assert TestClient(application).post("/api/auth/verify", json={"code": code}).status_code == 410
    assert client.post("/api/auth/verify", json={"code": code}).status_code == 204


def test_wrong_guesses_run_out_and_a_new_code_replaces_the_old(application, client):
    application.state.mailer = FakeMailbox()
    assert client.post("/api/auth/code", json={"email": "a@example.org"}).status_code == 204
    first = mailed_code(application)
    wrong = f"{(int(first) + 1) % 1_000_000:06d}"
    assert client.post("/api/auth/verify", json={"code": "12345"}).json()["error"]["code"] == "VALIDATION_FAILED"
    errors = [client.post("/api/auth/verify", json={"code": wrong}).json()["error"] for _ in range(5)]
    assert [e["code"] for e in errors] == ["CODE_INVALID"] * 4 + ["CODE_EXPIRED"]
    assert [e["details"].get("left") for e in errors[:4]] == [4, 3, 2, 1]
    # Five wrong guesses void the code, even the right one fails now.
    assert client.post("/api/auth/verify", json={"code": first}).status_code == 410
    assert client.post("/api/auth/code", json={"email": "a@example.org"}).status_code == 204
    second = mailed_code(application)
    if second != first:
        assert client.post("/api/auth/verify", json={"code": first}).status_code == 400
    assert client.post("/api/auth/verify", json={"code": second}).status_code == 204
    assert client.get("/api/me").json()["user"]["email"] == "a@example.org"


def test_code_expiry_and_rate_limit(application, client):
    mailbox = application.state.mailer = FakeMailbox()
    for address in ["a@example.org", "A@example.org", "a@EXAMPLE.org"]:
        assert client.post("/api/auth/code", json={"email": address}).status_code == 204
    assert client.post("/api/auth/code", json={"email": "a@example.org"}).status_code == 429
    assert len(mailbox.messages) == 3
    application.state.repo.db.execute("UPDATE login_code SET expires_at=0")
    assert client.post("/api/auth/verify", json={"code": mailed_code(application)}).status_code == 410
    assert client.get("/api/me").json()["user"] is None


def test_a_failed_send_voids_the_code(application, client):
    class Broken:
        async def send(self, *args, **kwargs):
            raise OSError("smtp down")

    application.state.mailer = Broken()
    assert client.post("/api/auth/code", json={"email": "a@example.org"}).status_code == 500
    assert application.state.repo.db.execute("SELECT count(*) FROM login_code").fetchone()[0] == 0


def test_the_letter_has_the_headers_spam_filters_expect():
    message = compose("robot@collabpad.ru", "a@example.org", "Subject", "text", "<p>html</p>")
    parsed = email.message_from_bytes(message.as_bytes())
    assert parsed["From"] == "CollabPad <robot@collabpad.ru>"
    assert parsed["Date"] and parsed["Message-ID"].endswith("@collabpad.ru>")
    assert [part.get_content_type() for part in parsed.walk()][1:] == ["text/plain", "text/html"]
    named = compose("Живой холст <robot@collabpad.ru>", "a@example.org", "S", "t", None)
    assert "robot@collabpad.ru" in named["From"] and not named.is_multipart()
    assert asyncio.iscoroutinefunction(FakeMailbox().send)
