"""T20: private worlds and the role matrix, over REST and over the socket."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.accounts import FakeMailbox, browser, give_plus, sign_in
from tests.conftest import payload

LASSO = {"lasso": [[100, 100], [320, 100], [320, 320], [100, 320]]}


def make_world(application: FastAPI, client: TestClient, title: str = "Farm") -> tuple[dict[str, Any], str]:
    host = sign_in(application, client, "host@example.org")
    give_plus(application, host["id"])
    created = client.post("/api/canvases", json={"title": title})
    assert created.status_code == 201 and created.json()["kind"] == "private"
    return host, created.json()["id"]


def test_only_plus_accounts_create_worlds_and_only_five(application: FastAPI, client: TestClient) -> None:
    assert client.post("/api/canvases", json={"title": "x"}).json()["error"]["code"] == "UNAUTHENTICATED"
    host = sign_in(application, client, "host@example.org")
    assert client.post("/api/canvases", json={"title": "x"}).json()["error"]["code"] == "PAYMENT_REQUIRED"
    give_plus(application, host["id"])
    assert client.post("/api/canvases", json={"title": ""}).json()["error"]["field"] == "title"
    ids = [client.post("/api/canvases", json={"title": f"w{n}"}).json()["id"] for n in range(5)]
    over = client.post("/api/canvases", json={"title": "six"})
    assert over.status_code == 409 and over.json()["error"]["details"] == {"limit": 5}
    mine = client.get("/api/canvases").json()
    assert [w["id"] for w in mine["owned"]] == ids and mine["owned"][0]["role"] == "editor"
    assert client.get(f"/api/canvases/{ids[0]}").json()["title"] == "w0"


def test_rest_role_matrix(application: FastAPI, client: TestClient) -> None:
    mailbox = application.state.mailer = FakeMailbox()
    host, world = make_world(application, client)
    guest, stranger = browser(application), browser(application)
    friend = sign_in(application, guest, "friend@example.org")

    def can(who: TestClient) -> tuple[int, int, int]:
        return (who.get(f"/api/canvases/{world}").status_code,
                who.get(f"/api/canvases/{world}/chunks/0/0").status_code,
                who.post(f"/api/canvases/{world}/alive", json=LASSO).status_code)  # fmt: skip

    # A new world is closed: no link role, no members.
    assert can(stranger) == (403, 403, 403) and can(guest) == (403, 403, 403)
    assert can(client) == (200, 200, 422)  # the owner edits; 422 = nothing inside the lasso

    # Only the owner manages access.
    for who in (guest, stranger):
        assert who.get(f"/api/canvases/{world}/access").status_code == 403
        assert who.put(f"/api/canvases/{world}/access", json={"linkRole": "editor"}).status_code == 403
        assert who.post(f"/api/canvases/{world}/members", json={"email": "x@example.org", "role": "editor"}).status_code == 403
    assert client.put(f"/api/canvases/{world}/access", json={"linkRole": "boss"}).status_code == 400

    # Link role viewer: anyone with the link watches, nobody else edits.
    assert client.put(f"/api/canvases/{world}/access", json={"linkRole": "viewer"}).json()["linkRole"] == "viewer"
    assert can(stranger) == (200, 200, 403)
    assert stranger.get(f"/api/canvases/{world}").json()["role"] == "viewer"

    # A member role above the link role wins; the invitation is mailed in the guest's language.
    invited = client.post(f"/api/canvases/{world}/members", json={"email": "Friend@Example.org", "role": "player"})
    assert invited.status_code == 201 and invited.json() == {
        "userId": friend["id"], "email": "friend@example.org", "role": "player", "mailed": True}
    assert mailbox.messages[-1][0] == "friend@example.org" and f"/p/{world}" in mailbox.messages[-1][2]
    assert guest.get(f"/api/canvases/{world}").json()["role"] == "player"
    assert can(guest) == (200, 200, 403)
    assert guest.get("/api/canvases").json()["shared"][0]["id"] == world
    assert client.post(f"/api/canvases/{world}/members", json={"email": "host@example.org", "role": "player"}).status_code == 400

    # Explicit member role overrides the link role (even if the link role is higher).
    changed = client.patch(f"/api/canvases/{world}/members/{friend['id']}", json={"role": "editor"})
    assert changed.json()["members"] == [{"userId": friend["id"], "email": "friend@example.org", "role": "editor"}]
    assert can(guest) == (200, 200, 422)
    client.patch(f"/api/canvases/{world}/members/{friend['id']}", json={"role": "viewer"})
    client.put(f"/api/canvases/{world}/access", json={"linkRole": "editor"})
    assert can(guest) == (200, 200, 403) and can(stranger) == (200, 200, 422)

    # Removing the member and closing the link shuts the world again.
    assert client.delete(f"/api/canvases/{world}/members/{friend['id']}").status_code == 204
    assert client.delete(f"/api/canvases/{world}/members/{friend['id']}").status_code == 404
    client.put(f"/api/canvases/{world}/access", json={"linkRole": "none"})
    assert can(guest) == (403, 403, 403)

    # Frozen: everyone who could come in watches, the owner included.
    client.put(f"/api/canvases/{world}/access", json={"linkRole": "editor"})
    application.state.repo.set_frozen(world, True)
    assert client.get(f"/api/canvases/{world}").json()["role"] == "viewer"
    assert can(client) == (200, 200, 403) and can(stranger) == (200, 200, 403)


def test_socket_role_matrix(application: FastAPI, client: TestClient) -> None:
    host, world = make_world(application, client)
    realtime, repo = application.state.realtime, application.state.repo
    realtime.sio.emit = AsyncMock()
    realtime.sio.enter_room = AsyncMock()
    realtime.sio.leave_room = AsyncMock()
    realtime.sio.disconnect = AsyncMock()
    guest = browser(application)
    friend = sign_in(application, guest, "friend@example.org")
    realtime.sessions["guest"] = repo.cookie_session(guest.cookies["cp_session"])["id"]
    realtime.sessions["owner"] = repo.cookie_session(client.cookies["cp_session"])["id"]
    view = {"canvasId": world, "chunks": [[0, 0]]}
    interact = realtime.acked("entity:interact", realtime.control.interact)
    claim = realtime.acked("control:claim", realtime.control.claim)

    async def code(reply: Any) -> str:
        reply = await reply
        return "ok" if reply.get("ok") else reply["error"]["code"]

    async def allowed(sid: str) -> tuple[str, str, str, str]:
        realtime.stroke_limit.buckets.clear()
        realtime.viewport_limit.buckets.clear()
        realtime.control.interact_limit.buckets.clear()
        return (await code(realtime.viewport(sid, view)),
                await code(interact(sid, {"entityId": "nobody", "action": "pet"})),
                await code(claim(sid, {"controllerId": "nobody"})),
                await code(realtime.add(sid, payload(world))))  # fmt: skip

    async def scenario() -> None:
        # NOT_FOUND / NOT_JOINED below mean "the role check passed".
        assert await allowed("owner") == ("ok", "NOT_FOUND", "NOT_FOUND", "ok")
        assert await allowed("guest") == ("FORBIDDEN_ROLE", "NOT_JOINED", "NOT_JOINED", "NOT_JOINED")
        assert await allowed("anon") == ("FORBIDDEN_ROLE", "NOT_JOINED", "NOT_JOINED", "NOT_JOINED")
        repo.set_link_role(world, "viewer")
        assert await allowed("anon") == ("ok", "FORBIDDEN_ROLE", "FORBIDDEN_ROLE", "FORBIDDEN_ROLE")
        repo.set_member(world, friend["id"], "player", host["id"])
        assert await allowed("guest") == ("ok", "NOT_FOUND", "NOT_FOUND", "FORBIDDEN_ROLE")
        repo.update_member(world, friend["id"], "editor")
        assert await allowed("guest") == ("ok", "NOT_FOUND", "NOT_FOUND", "ok")
        repo.set_frozen(world, True)
        assert await allowed("owner") == ("ok", "FORBIDDEN_ROLE", "FORBIDDEN_ROLE", "FORBIDDEN_ROLE")
        repo.set_frozen(world, False)
        # Losing access drops the sockets that were watching.
        repo.set_link_role(world, "none")
        repo.remove_member(world, friend["id"])
        await realtime.recheck(world)
        dropped = {call.args[0] for call in realtime.sio.disconnect.call_args_list}
        assert dropped == {"guest", "anon"}

    asyncio.run(scenario())


def test_locale_is_saved_for_the_session_and_the_account(application: FastAPI, client: TestClient) -> None:
    assert client.put("/api/me/locale", json={"locale": "de"}).status_code == 400
    assert client.put("/api/me/locale", json={"locale": "ru"}, headers={"Accept-Language": "en"}).status_code == 204
    assert client.get("/api/me").json()["locale"] == "ru"
    user = sign_in(application, client, "a@example.org")
    assert "Код для входа" in application.state.mailer.messages[-1][1]
    client.put("/api/me/locale", json={"locale": "en"})
    row = application.state.repo.db.execute("SELECT locale FROM app_user WHERE id=?", (user["id"],)).fetchone()
    assert row[0] == "en" and client.get("/api/me").json()["locale"] == "en"


def test_rename_delete_and_leave_private_worlds(application: FastAPI, client: TestClient) -> None:
    _, world = make_world(application, client, title="Old Farm")
    guest, stranger = browser(application), browser(application)
    friend = sign_in(application, guest, "friend@example.org")
    client.post(f"/api/canvases/{world}/members", json={"email": "friend@example.org", "role": "editor"})

    # Rename: owner only, non-empty after strip, never the shared world.
    assert guest.patch(f"/api/canvases/{world}", json={"title": "Hack"}).status_code == 403
    assert client.patch("/api/canvases/world", json={"title": "Mine"}).status_code == 403
    assert client.patch(f"/api/canvases/{world}", json={"title": "   "}).status_code == 400
    renamed = client.patch(f"/api/canvases/{world}", json={"title": "  New Farm  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "New Farm"
    assert client.get(f"/api/canvases/{world}").json()["title"] == "New Farm"

    # Changing a member's role online triggers realtime.recheck.
    recheck = AsyncMock()
    application.state.realtime.recheck = recheck
    assert client.patch(f"/api/canvases/{world}/members/{friend['id']}", json={"role": "viewer"}).status_code == 200
    recheck.assert_awaited_once_with(world)

    # Leave: unauthenticated 401, owner 403, non-member 404, member 204 + kick.
    assert stranger.delete(f"/api/canvases/{world}/members/me").status_code == 401
    sign_in(application, stranger, "stranger@example.org")
    assert stranger.delete(f"/api/canvases/{world}/members/me").status_code == 404
    assert client.delete(f"/api/canvases/{world}/members/me").status_code == 403
    kick = AsyncMock()
    application.state.realtime.kick = kick
    assert guest.delete(f"/api/canvases/{world}/members/me").status_code == 204
    kick.assert_awaited_once_with(world, friend["id"])
    assert guest.get("/api/canvases").json()["shared"] == []

    # Delete: owner only, never the shared world; frees the slot and drops sockets.
    assert guest.delete(f"/api/canvases/{world}").status_code == 403
    assert client.delete("/api/canvases/world").status_code == 403
    recheck.reset_mock()
    assert client.delete(f"/api/canvases/{world}").status_code == 204
    recheck.assert_awaited_once_with(world)
    assert client.get(f"/api/canvases/{world}").status_code == 404
    assert client.get("/api/canvases").json()["owned"] == []


def test_kick_emits_and_disconnects_member(application: FastAPI, client: TestClient) -> None:
    _, world = make_world(application, client)
    realtime, repo = application.state.realtime, application.state.repo
    realtime.sio.emit = AsyncMock()
    realtime.sio.disconnect = AsyncMock()
    guest = browser(application)
    friend = sign_in(application, guest, "friend@example.org")
    realtime.sessions["guest"] = repo.cookie_session(guest.cookies["cp_session"])["id"]
    realtime.sessions["owner"] = repo.cookie_session(client.cookies["cp_session"])["id"]
    realtime.rooms.viewports["guest"] = (world, set())
    realtime.rooms.viewports["owner"] = (world, set())
    asyncio.run(realtime.kick(world, friend["id"]))
    realtime.sio.emit.assert_awaited_once_with("canvas:kicked", {"canvasId": world}, to="guest")
    realtime.sio.disconnect.assert_awaited_once_with("guest")

