import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from app.i18n import translate


class FakeMailbox:
    def __init__(self):
        self.messages = []

    async def send(self, email, subject, body):
        self.messages.append((email, subject, body))


def test_magic_link_links_session_preserves_quota_and_rotates_cookie(application, client):
    mailbox = application.state.mailer = FakeMailbox()
    repo = application.state.repo
    anon = client.get("/api/me", headers={"Accept-Language": "ru"}).json()
    old_cookie = client.cookies["cp_session"]
    repo.spend_quota("s:" + old_cookie, "2026-01-01")
    address = {"email": " Tester@Example.org "}
    assert client.post("/api/auth/magic-link", json=address).status_code == 204
    email, subject, body = mailbox.messages[0]
    assert email == "tester@example.org"
    assert subject == translate(anon["locale"], "mail.login_subject")
    url = urlsplit(re.search(r"https?://\S+", body)[0])
    callback = url.path + "?" + url.query
    token = parse_qs(url.query)["token"][0]
    digest, = repo.db.execute("SELECT token_hash FROM magic_token").fetchone()
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    result = client.get(callback, follow_redirects=False)
    assert result.status_code == 302 and result.headers["location"] == "/w"
    assert "HttpOnly" in result.headers["set-cookie"] and "SameSite=lax" in result.headers["set-cookie"]
    credential = client.cookies["cp_session"]
    assert credential != old_cookie and repo.cookie_session(old_cookie) is None
    me = client.get("/api/me").json()
    assert me["user"]["email"] == email and me["quota"]["limit"] == 20
    assert repo.cookie_session(credential)["id"] == old_cookie
    assert repo.quota_used("u:" + me["user"]["id"], "2026-01-01") == 1
    assert client.get(callback).status_code == 401
    assert client.post("/api/auth/logout").status_code == 204
    assert repo.cookie_session(credential) is None
    assert client.get("/api/me").json()["user"] is None


def test_sign_in_returns_to_the_world_it_started_from(application, client):
    mailbox = application.state.mailer = FakeMailbox()
    bodies = [
        {"email": "a@example.org", "next": "/p/abc_DEF-1"},
        {"email": "b@example.org", "next": "https://evil.example/p/x"},
        {"email": "c@example.org", "next": "//evil.example"},
        {"email": "d@example.org"},
    ]
    for body in bodies:
        assert client.post("/api/auth/magic-link", json=body).status_code == 204
    landed = []
    for message in mailbox.messages:
        url = urlsplit(re.search(r"https?://\S+", message[2])[0])
        result = client.get(url.path + "?" + url.query, follow_redirects=False)
        landed.append(result.headers["location"])
    assert landed == ["/p/abc_DEF-1", "/w", "/w", "/w"]
    # A hand-edited callback cannot point elsewhere either.
    assert client.post("/api/auth/magic-link", json={"email": "e@example.org"}).status_code == 204
    url = urlsplit(re.search(r"https?://\S+", mailbox.messages[-1][2])[0])
    result = client.get(url.path + "?" + url.query + "&next=//evil.example", follow_redirects=False)
    assert result.headers["location"] == "/w"


def test_magic_link_expiry_and_rate_limit(application, client):
    mailbox = application.state.mailer = FakeMailbox()
    for email in ["a@example.org", "A@example.org", "a@EXAMPLE.org"]:
        assert client.post("/api/auth/magic-link", json={"email": email}).status_code == 204
    assert client.post("/api/auth/magic-link", json={"email": "a@example.org"}).status_code == 429
    assert len(mailbox.messages) == 3
    application.state.repo.db.execute("UPDATE magic_token SET expires_at=0")
    url = re.search(r"https?://\S+", mailbox.messages[-1][2])[0]
    assert client.get(url).status_code == 401
    assert client.get("/api/me").json()["user"] is None
