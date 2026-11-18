import asyncio
import json
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.realtime.rooms import Rooms
from tests.conftest import payload, private_canvas


def view(chunks: list[list[int]], canvas_id: str = "world") -> dict:
    return {"canvasId": canvas_id, "chunks": chunks}


def test_rooms_track_subscribers_per_chunk() -> None:
    rooms = Rooms()
    assert rooms.set_viewport("a", "world", frozenset({(0, 0), (1, 0)})) == (
        ["world:0:0", "world:1:0"],
        [],
    )
    assert rooms.set_viewport("b", "world", frozenset({(1, 0)})) == (["world:1:0"], [])
    assert rooms.set_viewport("a", "world", frozenset({(1, 0), (2, 0)})) == (
        ["world:2:0"],
        ["world:0:0"],
    )
    assert [rooms.count("world", c) for c in [(0, 0), (1, 0), (2, 0)]] == [0, 2, 1]
    # Moving to another canvas leaves every room of the old one.
    assert rooms.set_viewport("a", "other", frozenset({(1, 0)})) == (
        ["other:1:0"],
        ["world:1:0", "world:2:0"],
    )
    rooms.drop("a")
    rooms.drop("b")
    rooms.drop("never-joined")
    assert not rooms.subscribers and not rooms.viewports


def test_viewport_strokes_limits_and_errors(client: TestClient, application: FastAPI) -> None:
    realtime = application.state.realtime
    realtime.sio.emit = AsyncMock()
    realtime.sio.enter_room = AsyncMock()
    realtime.sio.leave_room = AsyncMock()

    async def scenario() -> None:
        assert (await realtime.add("a", payload()))["error"]["code"] == "NOT_JOINED"
        assert (await realtime.viewport("a", view([[0, 0]], "0123456789")))["error"][
            "code"
        ] == "NOT_FOUND"
        too_many = [[x, 0] for x in range(13)]
        assert (await realtime.viewport("a", view(too_many)))["error"]["field"] == "chunks"
        assert (await realtime.viewport("a", view([[-1, 0]])))["error"]["field"] == "chunks"
        realtime.viewport_limit.buckets.clear()
        # Chunks outside the world are dropped, not an error.
        assert (await realtime.viewport("a", view([[2, 4], [3, 4], [99, 0]]))) == {
            "ok": True,
            "lastSeq": 0,
        }
        entered = [call.args[1] for call in realtime.sio.enter_room.call_args_list]
        assert entered == ["world:2:4", "world:3:4"]
        assert (await realtime.viewport("a", view([[3, 4]])))["ok"]
        assert realtime.sio.leave_room.call_args.args == ("a", "world:2:4")
        assert (await realtime.add("a", payload("0123456789")))["error"][
            "code"
        ] == "NOT_JOINED"
        assert (await realtime.add("a", payload(color="blue")))["error"][
            "field"
        ] == "color"
        crossing = payload(points=[[1000, 5, 0.5], [1100, 5, 0.5]])
        assert (await realtime.add("a", crossing))["error"]["field"] == "points"
        # A client may draw in a chunk it is not subscribed to.
        stroke = payload(points=[[3000, 5000, 0.5], [3010, 5010, 0.5]])
        saved = {"ok": True, "seq": 1, "cx": 2, "cy": 4}
        assert (await realtime.add("a", stroke)) == saved
        assert (await realtime.add("a", stroke)) == saved
        emitted = [call.args[0] for call in realtime.sio.emit.call_args_list]
        # Each newly watched chunk gets a world:snapshot; the stroke goes out once.
        assert emitted == ["world:snapshot", "world:snapshot", "stroke:new"]
        snapshot = realtime.sio.emit.call_args_list[0]
        assert snapshot.args[1] == {"cx": 2, "cy": 4, "entities": []} and snapshot.kwargs == {"to": "a"}
        sent = realtime.sio.emit.call_args
        assert sent.kwargs == {"room": "world:2:4", "skip_sid": "a"}
        for _ in range(30):
            reply = await realtime.add("a", stroke)
        assert reply["error"]["code"] == "RATE_LIMITED"
        for _ in range(5):
            reply = await realtime.viewport("a", view([[3, 4]]))
        assert reply["error"]["code"] == "RATE_LIMITED"
        await realtime.disconnect("a")
        assert not realtime.rooms.viewports and not realtime.rooms.subscribers
        assert "a" not in realtime.stroke_limit.buckets

    asyncio.run(scenario())


def test_real_socketio_protocol_and_payload_limit(
    client: TestClient, application: FastAPI
) -> None:
    canvas = private_canvas(application.state.repo)
    with client.websocket_connect(
        "/socket.io/?EIO=4&transport=websocket", headers={"Upgrade": "websocket"}
    ) as ws:
        assert ws.receive_text().startswith("0{")
        ws.send_text("40")
        assert ws.receive_text().startswith("40{")
        ws.send_text("421" + json.dumps(["viewport:set", view([[0, 0]], canvas)]))
        snapshot = json.loads(ws.receive_text()[2:])
        assert snapshot == ["world:snapshot", {"cx": 0, "cy": 0, "entities": []}]
        assert json.loads(ws.receive_text()[3:])[0]["ok"]
        ws.send_text("422" + json.dumps(["stroke:add", payload(canvas)]))
        assert json.loads(ws.receive_text()[3:])[0]["seq"] == 1
        ws.send_text("423" + json.dumps(["stroke:add", {"large": "x" * 66000}]))
        ack = ws.receive_text()
        assert (
            ack.startswith("433")
            and json.loads(ack[3:])[0]["error"]["code"] == "PAYLOAD_TOO_LARGE"
        )
    assert client.post("/api/metrics/plus_clicked", content=b"x" * 65537).status_code == 413


def test_each_client_gets_one_delta_per_tick_with_only_its_chunks(application: FastAPI) -> None:
    realtime = application.state.realtime
    realtime.rooms.set_viewport("a", "world", frozenset({(0, 0), (1, 0)}))
    realtime.rooms.set_viewport("b", "world", frozenset({(1, 0), (5, 5)}))
    realtime.rooms.set_viewport("c", "world", frozenset({(9, 9)}))
    realtime.rooms.set_viewport("d", "other", frozenset({(0, 0)}))
    realtime.sio.manager.eio_sid_from_sid = lambda sid, namespace: f"eio-{sid}"
    sent: dict[str, str] = {}

    async def send(eio_sid: str, text: str) -> None:
        sent[eio_sid] = text

    realtime.sio.eio.send = send
    chunks = [
        {"cx": 0, "cy": 0, "upd": [["e1", 1.5, 2, "wander", "move", 100]], "gone": [], "enter": []},
        {"cx": 1, "cy": 0, "upd": [], "gone": ["e2"], "enter": [{"id": "ж", "label": {"ru": "корова"}}]},
    ]
    asyncio.run(realtime.emit_deltas("world", 42, chunks))
    assert set(sent) == {"eio-a", "eio-b"}  # c watches nothing that changed, d another canvas
    for eio_sid, expected in (("eio-a", chunks), ("eio-b", chunks[1:])):
        assert sent[eio_sid][0] == "2"  # a Socket.IO EVENT on the default namespace
        name, data = json.loads(sent[eio_sid][1:])
        assert name == "world:delta" and data["tick"] == 42
        assert sorted(data["chunks"], key=lambda c: c["cx"]) == expected


def test_events_are_encoded_without_a_binary_scan(application: FastAPI) -> None:
    packet_class = application.state.realtime.sio.packet_class
    assert packet_class.uses_binary_events is False
    packet = packet_class(data=["world:event", {"type": "eat", "rows": [[1, 2.5, "ok"]]}])
    assert packet.encode() == '2["world:event",{"type":"eat","rows":[[1,2.5,"ok"]]}]'
