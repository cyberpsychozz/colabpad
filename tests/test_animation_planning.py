"""Drawing geometry is fixed before the text-only motion planner runs."""

import asyncio
import json
from unittest.mock import patch

import pytest
from PIL import Image

from app.alive.choreography import DrawingParts, Motion, combine, compile_plan, validate_parts
from app.alive.preparation import prepare_atlas
from app.alive.rasterize import Cutout, render
from app.alive.vlm import FakeVlm, VlmError
from tests.animationkit import split_performance
from tests.conftest import stroke_in
from tests.test_choreography import performance

from tests.test_vlm import COW, adapter, reply
from tests.test_alive import make as make
from tests.test_generated_alive import submit


def anatomy():
    return {
        "version": 2, "status": "ok", "reason": None,
        "label": {"ru": "человечек", "en": "person"},
        "parts": [{
            "id": "right_arm", "label": {"ru": "рука справа", "en": "arm on the right"},
            "box": [0.6, 0.25, 0.35, 0.5], "pivot": [0.75, 0.5],
            "pivot_type": "joint", "order": 1, "kind": "part",
        }],
    }


def motion():
    return {
        "version": 1, "status": "ok", "reason": None, "duration": 1000,
        "tracks": [{"target": "right_arm", "property": "rotation", "keys": [
            {"t": 0, "v": 0, "ease": "smooth"},
            {"t": 500, "v": -60, "ease": "smooth"},
            {"t": 1000, "v": 0, "ease": "linear"},
        ]}], "repeats": [],
    }


def test_vision_identifies_parts_then_text_only_llm_receives_fixed_geometry():
    seen = []

    def handler(request, body, n):
        seen.append(body)
        return reply(anatomy() if n == 1 else motion())

    vlm, spent, models = adapter(handler)

    async def exercise():
        drawing = await vlm.identify_parts(b"drawing-png")
        return await vlm.animate(drawing, "машет рукой")

    result = asyncio.run(exercise())
    assert result.tracks[0].target == "right_arm"
    vision, planning = seen
    assert vision['provider'] == planning['provider'] == {'require_parameters': True}
    assert vision["response_format"]["json_schema"]["name"] == "drawing_parts_v2"
    assert vision["messages"][1]["content"][0]["type"] == "image_url"
    assert "машет рукой" not in json.dumps(vision, ensure_ascii=False)
    content = planning["messages"][1]["content"]
    assert all(item["type"] == "text" for item in content)
    assert json.loads(content[0]["text"])["drawing"] == anatomy()
    assert content[1]["text"] == "машет рукой"
    schema = planning["response_format"]["json_schema"]["schema"]
    assert "parts" not in schema["properties"] and "label" not in schema["properties"]
    assert schema["additionalProperties"] is False
    assert len(models) == 2 and next(iter(spent.values())) == 4000


def test_budget_exhausted_by_parts_prevents_motion_call():
    vlm, spent, models = adapter(lambda request, body, n: reply(anatomy()), cap=0.002)

    async def exercise():
        drawing = await vlm.identify_parts(b"png")
        await vlm.animate(drawing, "wave")

    with pytest.raises(VlmError, match="VLM_UNAVAILABLE"):
        asyncio.run(exercise())
    assert len(models) == 1 and spent


def arm_cutout():
    strokes = [
        {"color": "#000000", "width": 3, "points": [[50, 10, .5], [50, 90, .5]]},
        {"color": "#000000", "width": 3, "points": [[50, 40, .5], [90, 70, .5]]},
    ]
    return Cutout(Image.fromarray(render(strokes, 0, 0, 100, 100)), 0, 0, 1, strokes)


def test_whole_arm_and_actual_shoulder_replace_short_box_and_mid_arm_pivot():
    drawing = anatomy()
    drawing['parts'][0].update(box=[.64, .48, .32, .28], pivot=[.75, .59])
    prepared, atlas, manifest = prepare_atlas(arm_cutout(), DrawingParts.model_validate(drawing))
    arm = prepared.parts[0]
    assert arm.pivot == pytest.approx((.5, .4))
    assert arm.box[0] < .5 and arm.box[0] + arm.box[2] > .9
    layer = next(l for l in manifest['layers'] if l['id'] == 'right_arm')
    assert layer['pivot'] == pytest.approx([0, -10])
    # Preparation does not mutate the provider's original geometry.
    assert drawing['parts'][0]['pivot'] == [.75, .59]


def test_disconnected_joint_is_rejected_instead_of_rotating_mid_limb():
    cutout = arm_cutout()
    cutout.strokes[1]['points'] = [[70, 40, .5], [90, 70, .5]]
    with pytest.raises(ValueError, match='does not touch'):
        prepare_atlas(cutout, DrawingParts.model_validate(anatomy()))


def head_cutout():
    # The head is a closed outline started at its top. Its neck joins the bottom,
    # away from both stored endpoints of that outline.
    import math
    head = [[50 + 15 * math.sin(i * math.tau / 80),
             30 - 15 * math.cos(i * math.tau / 80), .5] for i in range(81)]
    strokes = [
        {"color": "#000000", "width": 3, "points": head},
        {"color": "#000000", "width": 3, "points": [[50, 45, .5], [50, 90, .5]]},
    ]
    return Cutout(Image.fromarray(render(strokes, 0, 0, 100, 100)), 0, 0, 1, strokes)


def head_anatomy():
    drawing = anatomy()
    drawing['parts'][0].update(id='head', box=[.3, .1, .4, .35], pivot=[.5, .45])
    return DrawingParts.model_validate(drawing)


def test_closed_head_joins_at_neck_even_when_stroke_starts_at_top():
    prepared, _, manifest = prepare_atlas(head_cutout(), head_anatomy())
    assert prepared.parts[0].pivot[0] == pytest.approx(.5, abs=.02)
    assert prepared.parts[0].pivot[1] == pytest.approx(.45, abs=.02)
    assert next(l for l in manifest['layers'] if l['id'] == 'head')['pivot'][1] < 0


def test_geometry_failure_is_logged_with_part_and_stage_and_saved_for_diagnostics(make, caplog):
    drawing = anatomy()
    drawing['parts'][0].update(box=[.4, .4, .1, .1])
    world, client = make(FakeVlm(drawing))
    world.draw()
    status = submit(client)
    assert status['error'] == 'ANIMATION_INVALID'
    failed = world.last('alive:failed')
    trace = json.loads(world.app.state.repo.get_job(failed['jobId'])['vlm_result'])
    failure = trace['failure']
    assert failure['stage'] == 'prepare'
    assert 'right_arm' in failure['reason']
    assert 'no lines' in failure['reason']
    assert 'stage=prepare' in caplog.text
    assert 'code=ANIMATION_INVALID' in caplog.text
    assert failed['jobId'] in caplog.text


@pytest.mark.parametrize('geometry,field', [
    ({'box': [.6, .25, .5, .5]}, 'box.right'),
    ({'box': [.6, .25, .35, .8]}, 'box.bottom'),
    ({'box': [-.01, .25, .35, .5]}, 'box.x'),
    ({'box': [.6, .25, 0, .5]}, 'box'),
    ({'pivot': [1.01, .5]}, 'pivot.x'),
])
def test_invalid_identified_geometry_uses_provider_retries_and_fallback(geometry, field, caplog):
    invalid = anatomy()
    invalid['parts'][0].update(geometry)
    vlm, spent, models = adapter(lambda request, body, n: reply(
        invalid if body['model'].startswith('google/') else anatomy()))
    drawing = asyncio.run(vlm.identify_parts(b'private-png'))
    assert drawing.model_dump(mode='json') == anatomy()
    assert models == ['google/gemini-3.8-flash'] * 2 + ['qwen/qwen3-vl-235b-a22b-instruct']
    assert next(iter(spent.values())) == 6000
    assert 'part=right_arm' in caplog.text and field in caplog.text
    assert 'private-png' not in caplog.text


def test_valid_geometry_after_bad_first_response_spawns_with_one_user_request(make):
    drawing, movement = split_performance(performance())
    invalid = anatomy()
    invalid['parts'][0]['box'] = [.6, .25, .5, .5]

    def handler(request, body, n):
        if body['response_format']['json_schema']['name'] == 'motion_v1':
            return reply(movement)
        return reply(invalid if n == 1 else drawing)

    vlm, spent, models = adapter(handler)
    world, client = make(vlm)
    world.draw()
    assert submit(client)['status'] == 'spawned'
    assert len(models) == 3 and next(iter(spent.values())) == 6000
    assert 'alive:failed' not in world.names()
    assert client.get('/api/me').json()['quota']['used'] == 1
    assert not world.app.state.alive.locks.owner


def test_exhausted_geometry_retries_leave_strokes_and_refund(make, caplog):
    invalid = anatomy()
    invalid['parts'][0]['box'] = [.6, .25, .5, .5]
    vlm, spent, models = adapter(lambda request, body, n: reply(invalid))
    world, client = make(vlm)
    seqs = world.draw()
    assert submit(client)['error'] == 'ANIMATION_INVALID'
    assert len(models) == 3 and next(iter(spent.values())) == 6000
    assert 'alive:parts' not in world.names()
    assert 'stage=parts' in caplog.text and 'part=right_arm' in caplog.text
    assert 'box.right' in caplog.text
    assert world.app.state.repo.static_seqs('world', seqs) == set(seqs)
    assert not world.app.state.alive.locks.owner
    assert client.get('/api/me').json()['quota']['used'] == 0


def test_geometry_retries_stop_at_daily_provider_budget():
    invalid = anatomy()
    invalid['parts'][0]['box'] = [.6, .25, .5, .5]
    vlm, spent, models = adapter(lambda request, body, n: reply(invalid), cap=.002)
    with pytest.raises(VlmError, match='daily budget reached'):
        asyncio.run(vlm.identify_parts(b'png'))
    assert len(models) == 1 and next(iter(spent.values())) == 2000


def test_geometry_roundoff_at_raster_edge_prepares_and_compiles():
    # A layer touching the raster's right edge has x0=1, width=21. Its actual
    # world -> raster round trip exceeds 1 by one ULP during preparation.
    size, scale = 22, .137
    strokes = [{
        'color': '#000000', 'width': 3 / scale,
        'points': [[2.5 / scale, 11 / scale, .5], [21 / scale, 11 / scale, .5]],
    }]
    cutout = Cutout(Image.fromarray(render(strokes, 0, 0, size, size, scale)),
                    0, 0, scale, strokes)
    drawing = anatomy()
    drawing['parts'][0].update(box=[0, 0, 1, 1], pivot_type='center')
    prepared, _, _ = prepare_atlas(cutout, DrawingParts.model_validate(drawing))
    x, _, width, _ = prepared.parts[0].box
    assert x + width > 1
    validate_parts(prepared.parts)
    compile_plan(combine(prepared, Motion.model_validate(motion())), 100, 100)


@pytest.mark.parametrize('geometry,field', [
    ({'box': [.6, .25, .40000001, .5]}, 'box.right'),
    ({'pivot': [.5, -.00000001]}, 'pivot.y'),
    ({'box': [.6, .25, -.0000000001, .5]}, 'box'),
])
def test_geometry_roundoff_tolerance_keeps_real_errors_invalid(geometry, field):
    drawing = anatomy()
    drawing['parts'][0].update(geometry)
    with pytest.raises(ValueError, match=f'part=right_arm:.*{field}'):
        prepare_atlas(arm_cutout(), DrawingParts.model_validate(drawing))


def test_provider_contract_error_names_field_without_dumping_drawing(caplog):
    drawing = anatomy()
    drawing['parts'][0]['pivot_type'] = 'private-invalid-value'
    drawing['label']['ru'] = 'private-drawing-label'
    vlm, _, _ = adapter(lambda request, body, n: reply(drawing))
    with pytest.raises(VlmError) as error:
        asyncio.run(vlm.identify_parts(b'private-png'))
    assert 'parts.0.pivot_type' in str(error.value)
    assert 'literal_error' in caplog.text
    assert 'private-invalid-value' not in caplog.text
    assert 'private-drawing-label' not in caplog.text
    assert 'private-png' not in caplog.text


def test_provider_http_error_explains_rejection_without_exposing_key(caplog):
    import httpx
    vlm, _, _ = adapter(lambda request, body, n: httpx.Response(
        400, json={'error': {'message': 'response_format unsupported; key=test-key'}}))
    with pytest.raises(VlmError):
        asyncio.run(vlm.identify_parts(b'png'))
    assert 'HTTP 400' in caplog.text
    assert 'response_format unsupported' in caplog.text
    assert 'test-key' not in caplog.text


def test_motion_http_400_goes_directly_to_fallback_and_logs_nested_provider_reason(caplog):
    import httpx

    def handler(request, body, n):
        if body['model'].startswith('google/'):
            return httpx.Response(400, json={'error': {
                'message': 'Provider returned error',
                'metadata': {'provider_name': 'Google', 'raw': json.dumps({
                    'error': {'message': 'Unsupported schema keyword exclusiveMinimum; key=test-key'}
                })},
            }})
        return reply(motion())

    vlm, _, models = adapter(handler)
    result = asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave'))
    assert result.status == 'ok'
    assert models == ['google/gemini-3.8-flash', 'qwen/qwen3-vl-235b-a22b-instruct']
    assert 'Unsupported schema keyword exclusiveMinimum' in caplog.text
    assert 'test-key' not in caplog.text


@pytest.mark.parametrize('status', [408, 429, 503])
def test_transient_http_failures_still_retry_the_primary(status):
    import httpx
    vlm, _, models = adapter(lambda request, body, n: httpx.Response(status) if n == 1 else reply(motion()))
    assert asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave')).status == 'ok'
    assert models == ['google/gemini-3.8-flash', 'google/gemini-3.8-flash']


def test_primary_parts_timeout_then_motion_400_can_complete_on_fallback(make):
    import httpx
    drawing, movement = split_performance(performance())

    def handler(request, body, n):
        parts = body['response_format']['json_schema']['name'] == 'drawing_parts_v2'
        if body['model'].startswith('google/'):
            if parts:
                raise httpx.ReadTimeout('slow primary', request=request)
            return httpx.Response(400, json={'error': {'message': 'Provider returned error'}})
        return reply(drawing if parts else movement)

    vlm, _, models = adapter(handler)
    world, client = make(vlm)
    world.draw()
    assert submit(client)['status'] == 'spawned'
    assert models == ['google/gemini-3.8-flash', 'qwen/qwen3-vl-235b-a22b-instruct'] * 2
    assert client.get('/api/me').json()['quota']['used'] == 1
    assert not world.app.state.alive.locks.owner


def test_motion_wire_schema_uses_supported_inclusive_integer_bound():
    schema = Motion.model_json_schema()
    end = schema['$defs']['Repeat']['properties']['end']
    assert end['minimum'] == 1 and 'exclusiveMinimum' not in end
    from app.alive.choreography import Repeat
    with pytest.raises(ValueError):
        Repeat(start=0, end=0, count=2)


def test_gemini_accepts_motion_schema_without_large_nested_array_limits():
    import httpx
    schemas = []

    def handler(request, body, n):
        schema = body['response_format']['json_schema']['schema']
        schemas.append(schema)
        keys = schema['$defs']['Track']['properties']['keys']
        if not body['model'].startswith('google/') or 'maxItems' in keys:
            return httpx.Response(400, json={'error': {
                'message': 'The specified schema produces a constraint that has too many states for serving.'
            }})
        return reply(motion())

    vlm, spent, models = adapter(handler)
    assert asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave')).status == 'ok'
    assert models == ['google/gemini-3.8-flash']
    schema = schemas[0]
    assert 'maxItems' not in schema['properties']['tracks']
    assert 'maxItems' not in schema['properties']['repeats']
    assert schema['$defs']['Track']['properties']['keys']['minItems'] == 2
    assert schema['additionalProperties'] is False
    assert next(iter(spent.values())) == 2000


def test_other_motion_providers_keep_full_limits_and_schema_is_not_mutated():
    seen = []
    vlm, _, _ = adapter(lambda request, body, n: (seen.append(body) or reply(motion())))

    async def exercise():
        drawing = DrawingParts.model_validate(anatomy())
        await vlm.animate(drawing, 'wave')
        vlm.config = {**vlm.config, 'motion_model': 'qwen/qwen3-vl-235b-a22b-instruct'}
        await vlm.animate(drawing, 'wave')

    asyncio.run(exercise())
    schema = seen[1]['response_format']['json_schema']['schema']
    assert schema['properties']['tracks']['maxItems'] == 90
    assert schema['properties']['repeats']['maxItems'] == 8
    assert schema['$defs']['Track']['properties']['keys']['maxItems'] == 256
    assert Motion.model_json_schema()['$defs']['Track']['properties']['keys']['maxItems'] == 256


@pytest.mark.parametrize('oversized', ['keys', 'tracks', 'repeats'])
def test_compact_gemini_schema_still_rejects_oversized_model_output(oversized):
    data = motion()
    if oversized == 'keys':
        data['tracks'][0]['keys'] = [
            {'t': t, 'v': 0, 'ease': 'linear'} for t in range(257)
        ]
    elif oversized == 'tracks':
        data['tracks'] = data['tracks'] * 91
    else:
        data['repeats'] = [{'start': 0, 'end': 1000, 'count': 2}] * 9
    vlm, spent, models = adapter(lambda request, body, n: reply(data))
    with pytest.raises(VlmError, match='ANIMATION_INVALID'):
        asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave'))
    assert len(models) == 3 and next(iter(spent.values())) == 6000


def test_generation_http_timeouts_are_separate_from_ordinary_classification():
    seen = []

    def handler(request, body, n):
        seen.append(request.extensions['timeout']['read'])
        name = body['response_format']['json_schema']['name']
        return reply({'drawing': COW, 'drawing_parts_v2': anatomy(), 'motion_v1': motion()}[name])

    vlm, _, _ = adapter(handler)

    async def exercise():
        await vlm.classify(b'png')
        drawing = await vlm.identify_parts(b'png')
        await vlm.animate(drawing, 'wave')

    asyncio.run(exercise())
    assert seen == [10, 30, 30]


@pytest.mark.parametrize('hard,status', [(.3, 'spawned'), (.06, 'failed')])
def test_two_generation_stages_share_their_own_deadline(make, monkeypatch, hard, status):
    drawing, movement = split_performance(performance())

    class SlowStages(FakeVlm):
        async def identify_parts(self, png):
            await asyncio.sleep(.04)
            return DrawingParts.model_validate(drawing)

        async def animate(self, drawing, description):
            await asyncio.sleep(.04)
            return Motion.model_validate(movement)

    monkeypatch.setattr('app.alive.queue.RIG_RESERVE_S', .005)
    world, client = make(SlowStages())
    world.draw()
    world.limits['hard_timeout_s'] = .05
    world.app.state.config.vlm['generation']['hard_timeout_s'] = hard
    result = submit(client)
    assert result['status'] == status
    assert client.get('/api/me').json()['quota']['used'] == (1 if status == 'spawned' else 0)
    if status == 'failed':
        assert result['error'] == 'VLM_TIMEOUT'


def test_provider_refusal_logs_structured_reason_before_preview(make, caplog):
    drawing = anatomy()
    drawing.update(status='unsupported', reason='cannot_satisfy', parts=[])
    world, client = make(FakeVlm(drawing))
    world.draw()
    assert submit(client)['error'] == 'ANIMATION_UNSUPPORTED'
    assert 'alive:parts' not in world.names()
    assert 'stage=parts' in caplog.text
    assert 'provider status=unsupported reason=cannot_satisfy' in caplog.text


def test_motion_cannot_replace_geometry_even_with_valid_part_numbers():
    malicious = {**motion(), 'parts': anatomy()['parts']}
    vlm, spent, models = adapter(lambda request, body, n: reply(malicious))
    with pytest.raises(VlmError, match='ANIMATION_INVALID'):
        asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave'))
    assert len(models) == 3 and spent


def test_motion_can_use_a_separate_text_model():
    vlm, spent, models = adapter(lambda request, body, n: reply(motion()))
    vlm.config = {**vlm.config, 'motion_model': 'text-planner'}
    asyncio.run(vlm.animate(DrawingParts.model_validate(anatomy()), 'wave'))
    assert models == ['text-planner']


def test_pipeline_fixes_geometry_before_planning_and_keeps_both_stage_results(make):

    class Planner(FakeVlm):
        async def animate(self, drawing, description):
            assert drawing.parts[0].pivot == pytest.approx((.5, .4))
            assert drawing.parts[0].box[0] < .5
            # The planner gets a copy: a provider cannot mutate prepared layers.
            drawing.parts[0].pivot = (.9, .9)
            return Motion.model_validate(motion())

    source = anatomy()
    source['parts'][0].update(box=[.64, .48, .32, .28], pivot=[.75, .59])
    world, client = make(Planner(source))
    repo = world.app.state.repo
    for stroke in arm_cutout().strokes:
        repo.insert_stroke(stroke_in(points=[[x + 150, y + 150, p] for x,y,p in stroke['points']], width=3), (0, 0))
    # Use a fixed 100x100 raster here to make the bad shoulder fixture exact.
    cutout = arm_cutout()
    cutout.x = cutout.y = 150
    for stroke in cutout.strokes:
        stroke['points'] = [[x+150, y+150, p] for x,y,p in stroke['points']]
    with patch('app.alive.queue.rasterize', return_value=cutout):
        status = submit(client, 'машет рукой')
    assert status['status'] == 'spawned'
    event = next(item for item in world.events if item[0] == 'alive:parts')
    assert len(event[2]) == 1 and not event[2][0].startswith('world:')
    assert event[1]['drawing']['parts'][0]['pivot'] == pytest.approx([.5, .4])
    entity = repo.get_entity(status['entityId'])
    layer = next(l for l in entity['sprite']['manifest']['layers'] if l['id'] == 'right_arm')
    assert layer['pivot'] == pytest.approx([0, -10])
    trace = json.loads(repo.get_job(event[1]['jobId'])['vlm_result'])
    assert trace['identified']['parts'][0]['pivot'] == [.75, .59]
    assert trace['prepared']['parts'][0]['pivot'] == pytest.approx([.5, .4])
    assert trace['motion']['tracks'][0]['target'] == 'right_arm'


def test_motion_failure_after_parts_leaves_strokes_and_refunds(make):
    drawing, _ = split_performance(performance())
    world, client = make(FakeVlm(drawing, VlmError('VLM_UNAVAILABLE')))
    seqs = world.draw()
    assert submit(client)['error'] == 'VLM_UNAVAILABLE'
    assert 'alive:parts' in world.names()
    assert world.app.state.repo.static_seqs('world', seqs) == set(seqs)
    assert not world.app.state.alive.locks.owner
    assert client.get('/api/me').json()['quota']['used'] == 0
