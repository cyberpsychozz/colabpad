import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient


def payload(canvas_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "canvasId": canvas_id,
        "clientId": str(uuid4()),
        "color": "#1E88E5",
        "width": 6,
        "points": [[10, 20, 0.5], [30, 40, 0.8]],
        **changes,
    }


def test_realtime_limits_and_errors(client: TestClient, application: FastAPI) -> None:
    realtime = application.state.realtime
    realtime.sio.emit = AsyncMock()
    realtime.sio.enter_room = AsyncMock()
    realtime.sio.leave_room = AsyncMock()
    canvas = application.state.repo.create_canvas()["id"]

    async def scenario() -> None:
        assert (await realtime.add("a", payload(canvas)))["error"][
            "code"
        ] == "NOT_JOINED"
        assert (await realtime.join("a", {"canvasId": "0123456789"}))["error"][
            "code"
        ] == "CANVAS_NOT_FOUND"
        assert (await realtime.join("a", {"canvasId": canvas})) == {
            "ok": True,
            "lastSeq": 0,
        }
        assert (await realtime.add("a", payload("0123456789")))["error"][
            "code"
        ] == "NOT_JOINED"
        assert (await realtime.add("a", payload(canvas, color="blue")))["error"][
            "field"
        ] == "color"
        stroke = payload(canvas)
        assert (await realtime.add("a", stroke)) == {"ok": True, "seq": 1}
        assert (await realtime.add("a", stroke)) == {"ok": True, "seq": 1}
        emitted = [call.args[0] for call in realtime.sio.emit.call_args_list]
        assert emitted == ["stroke:new"]
        assert realtime.sio.emit.call_args.kwargs["skip_sid"] == "a"
        for _ in range(30):
            reply = await realtime.add("a", stroke)
        assert reply["error"]["code"] == "RATE_LIMITED"
        await realtime.disconnect("a")
        assert "a" not in realtime.members and "a" not in realtime.stroke_limit.buckets

    asyncio.run(scenario())


def test_real_socketio_protocol_and_payload_limit(
    client: TestClient, application: FastAPI
) -> None:
    canvas = application.state.repo.create_canvas()["id"]
    with client.websocket_connect(
        "/socket.io/?EIO=4&transport=websocket", headers={"Upgrade": "websocket"}
    ) as ws:
        assert ws.receive_text().startswith("0{")
        ws.send_text("40")
        assert ws.receive_text().startswith("40{")
        ws.send_text("421" + json.dumps(["canvas:join", {"canvasId": canvas}]))
        assert json.loads(ws.receive_text()[3:])[0]["ok"]
        ws.send_text("422" + json.dumps(["stroke:add", payload(canvas)]))
        assert json.loads(ws.receive_text()[3:])[0] == {"ok": True, "seq": 1}
        ws.send_text("423" + json.dumps(["stroke:add", {"large": "x" * 66000}]))
        ack = ws.receive_text()
        assert (
            ack.startswith("433")
            and json.loads(ack[3:])[0]["error"]["code"] == "PAYLOAD_TOO_LARGE"
        )
    assert client.post("/api/metrics/plus_clicked", content=b"x" * 65537).status_code == 413
