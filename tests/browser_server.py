"""Playwright server: manual classification and deterministic generated performances.

Only the browser runner imports this; production has no fake-provider setting.
"""

import asyncio
import io
import json

import httpx
import uvicorn
from PIL import Image

from app.alive.choreography import DrawingParts, Motion
from app.alive.vlm import Budget, OpenRouterVlm, UnavailableVlm, VlmError
from app.config import GameConfig, Settings
from app.main import create_app, create_asgi


class BrowserVlm(UnavailableVlm):
    generation_prompt_version = "browser-motion-1"
    parts_prompt_version = "browser-parts-2"

    async def identify_parts(self, image_png: bytes) -> DrawingParts:
        await asyncio.sleep(0.15)
        with Image.open(io.BytesIO(image_png)) as image:
            tall = image.height > image.width * 1.5
            wide = image.width > image.height * 2.2
        if wide:
            drawing = DrawingParts.model_validate({
                'version': 2, 'status': 'ok', 'reason': None,
                'label': {'ru': 'машина', 'en': 'car'},
                'parts': [{'id': 'roller', 'kind': 'wheel', 'box': [.34, .12, .32, .76],
                           'pivot': [.5, .5], 'order': 1, 'pivot_type': 'center',
                           'label': {'ru': 'колесо', 'en': 'wheel'}}],
            })
            config = GameConfig(Settings().config_dir).vlm
            budget = Budget(1, lambda key: 0, lambda key, amount: None)

            def respond(request):
                body = json.loads(request.content)
                result = drawing.model_dump(mode='json')
                if body['model'] == config['primary_model']:
                    # Structurally valid JSON, but x + width exceeds the image.
                    # The adapter must retry/fall back inside this same ALIVE job.
                    result['parts'][0]['box'][2] = .76
                return httpx.Response(200, json={
                    'choices': [{'message': {'content': json.dumps(result)}}],
                    'usage': {'cost': .002},
                })

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                vision = OpenRouterVlm(client, config, 'browser-fixture', ('fixture', ''), budget)
                return await vision.identify_parts(image_png)
        if tall:
            return DrawingParts.model_validate({
                'version': 2, 'status': 'ok', 'reason': None,
                'label': {'ru': 'человечек', 'en': 'person'},
                'parts': [{'id': 'head', 'box': [0, 0, 1, .37],
                           'pivot': [.5, .36], 'order': 1, 'pivot_type': 'joint', 'kind': 'part',
                           'label': {'ru': 'голова', 'en': 'head'}}],
            })
        return DrawingParts.model_validate({
            "version": 2, "status": "ok", "reason": None,
            "label": {"ru": "исполнитель", "en": "performer"},
            "parts": [{"id": "left_arm", "box": [0, 0, 0.45, 1],
                       "pivot": [0.35, 0.5], "order": 1, "pivot_type": "joint", "kind": "part",
                       "label": {"ru": "рука слева", "en": "arm on the left"}}],
        })

    async def animate(self, drawing: DrawingParts, description: str) -> Motion:
        await asyncio.sleep(0.5)
        if description == "unavailable":
            raise VlmError("VLM_UNAVAILABLE")
        if description == "invalid":
            raise VlmError("ANIMATION_INVALID")
        if drawing.parts[0].kind == 'wheel':
            return Motion.model_validate({
                'version': 1, 'status': 'ok', 'reason': None, 'duration': 2000,
                'tracks': [{'target': 'roller', 'property': 'rotation', 'keys': [
                    {'t': 0, 'v': 0, 'ease': 'linear'},
                    {'t': 2000, 'v': 360, 'ease': 'linear'},
                ]}], 'repeats': [],
            })
        if drawing.parts[0].id == 'head':
            return Motion.model_validate({
                'version': 1, 'status': 'ok', 'reason': None, 'duration': 1000,
                'tracks': [{'target': 'head', 'property': 'rotation', 'keys': [
                    {'t': 0, 'v': 0, 'ease': 'smooth'},
                    {'t': 500, 'v': 20, 'ease': 'smooth'},
                    {'t': 1000, 'v': 0, 'ease': 'linear'},
                ]}], 'repeats': [],
            })
        performance = Motion.model_validate(
            {
                "version": 1,
                "status": "unsupported" if description == "other actors" else "ok",
                "reason": "other_actors" if description == "other actors" else None,
                "duration": 30000 if description == "recovery" else 12000,
                "repeats": [],
                "tracks": [
                    {
                        "target": "root",
                        "property": "x",
                        "keys": [
                            {"t": 0, "v": 0, "ease": "linear"},
                            {"t": 12000, "v": 1, "ease": "linear"},
                        ],
                    },
                    {
                        "target": "body",
                        "property": "y",
                        "keys": [
                            {"t": 0, "v": 0, "ease": "smooth"},
                            {"t": 4000, "v": -0.4, "ease": "smooth"},
                            {"t": 8000, "v": 0, "ease": "linear"},
                            {"t": 12000, "v": 0, "ease": "linear"},
                        ],
                    },
                    {
                        "target": "left_arm",
                        "property": "rotation",
                        "keys": [
                            {"t": 0, "v": 0, "ease": "smooth"},
                            {"t": 3000, "v": -90, "ease": "smooth"},
                            {"t": 6000, "v": 30, "ease": "smooth"},
                            {"t": 9000, "v": -60, "ease": "smooth"},
                            {"t": 12000, "v": 0, "ease": "linear"},
                        ],
                    },
                ],
            }
        )
        if description in ('provider fallback', 'provider schema'):
            config = GameConfig(Settings().config_dir).vlm
            spent = {}
            budget = Budget(1, lambda key: spent.get(key, 0),
                            lambda key, amount: spent.__setitem__(key, spent.get(key, 0) + amount))

            def respond(request):
                body = json.loads(request.content)
                if description == 'provider schema':
                    schema = body['response_format']['json_schema']['schema']
                    keys = schema['$defs']['Track']['properties']['keys']
                    if body['model'] != config['motion_model'] or 'maxItems' in keys:
                        return httpx.Response(400, json={'error': {
                            'message': 'The specified schema produces a constraint that has too many states for serving.'
                        }})
                elif body['model'] == config['motion_model']:
                    return httpx.Response(400, json={'error': {'message': 'Provider returned error'}})
                return httpx.Response(200, json={
                    'choices': [{'message': {'content': performance.model_dump_json()}}],
                    'usage': {'cost': .002},
                })

            # Exercise the real adapter, compact schema and failover offline.
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                planner = OpenRouterVlm(client, config, 'browser-fixture', ('fixture', ''), budget)
                return await planner.animate(drawing, description)
        return performance


if __name__ == "__main__":
    settings = Settings()
    application = create_app(settings, vlm=BrowserVlm())

    @application.post("/_test/freeze/{frozen}")
    async def freeze(frozen: bool):
        # This server is started only by Playwright with its throwaway database.
        state = application.state
        state.repo.db.execute(
            "UPDATE canvas SET frozen=? WHERE id='world'", (int(frozen),)
        )
        state.sim.send({"op": "freeze", "canvas": "world", "frozen": frozen})
        await state.realtime.frozen("world", frozen)
        return {"ok": True}

    @application.post("/_test/restart-sim")
    async def restart_sim():
        sim = application.state.sim
        old = sim.restarts
        sim.transport.process.kill()
        for _ in range(100):
            await asyncio.sleep(0.05)
            if sim.restarts > old:
                return {"ok": True}
        raise RuntimeError("simulation did not recover")

    uvicorn.run(
        create_asgi(application), host="0.0.0.0", port=settings.port, access_log=False
    )
