import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from app.alive.postprocess import clamp_box, postprocess, top_choices
from app.alive.vlm import (
    Budget,
    FakeVlm,
    OpenRouterVlm,
    UnavailableVlm,
    VlmError,
    VlmResult,
    load_prompt,
)
from app.errors import AppError

ROOT = Path(__file__).parent.parent
CONFIG = yaml.safe_load((ROOT / "config" / "vlm.yaml").read_text())
LABELS = yaml.safe_load((ROOT / "config" / "labels.yaml").read_text())
SIZES = {"S": 96, "M": 256}

COW: dict[str, Any] = {
    "type": "creature",
    "confidence": 0.86,
    "top3": [{"type": "creature", "p": 0.86}, {"type": "predator", "p": 0.1}],
    "label": {"ru": "корова", "en": "cow"},
    "locomotion": "walk",
    "diet": "plants",
    "role": "null",
    "variant": None,
    "facing": "left",
    "multiple": False,
    "unsafe": False,
    "parts": {
        "legs": [[0.2, 0.6, 0.1, 0.4], [0.7, 0.6, 0.1, 0.4]],
        "wings": [],
        "wheels": [],
        "head": [0.0, 0.1, 0.25, 0.3],
        "tail": None,
    },
}


def reply(content: Any, cost: float | None = 0.002) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    usage = {"cost": cost} if cost is not None else {}
    return httpx.Response(
        200, json={"choices": [{"message": {"content": text}}], "usage": usage}
    )


def adapter(handler: Any, cap: float = 5.0) -> tuple[OpenRouterVlm, dict[str, int], list[str]]:
    spent: dict[str, int] = {}
    models: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        return handler(request, body, len(models))

    budget = Budget(
        cap,
        lambda key: spent.get(key, 0),
        lambda key, amount: spent.__setitem__(key, spent.get(key, 0) + amount),
        today=lambda: "2026-11-16",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    prompt = load_prompt(ROOT / "prompts" / "classify.md")
    return OpenRouterVlm(client, CONFIG, "test-key", prompt, budget), spent, models


def test_schema_accepts_the_contract_and_rejects_nonsense() -> None:
    cow = VlmResult.model_validate(COW)
    assert cow.role is None and cow.parts.head == (0.0, 0.1, 0.25, 0.3)
    for bad in [
        {**COW, "type": "unicorn"},
        {**COW, "confidence": 1.5},
        {**COW, "label": {"ru": "корова"}},
        {**COW, "facing": "up"},
        {**COW, "parts": {"legs": [[0.1, 0.2, 0.3]]}},
    ]:
        with pytest.raises(ValueError):
            VlmResult.model_validate(bad)
    version, prompt = load_prompt(ROOT / "prompts" / "classify.md")
    assert version == "2" and "FUNCTION" in prompt and "version:" not in prompt


def test_request_shape_and_cost_tracking() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request, body: dict[str, Any], n: int) -> httpx.Response:
        seen.update(body, auth=request.headers["authorization"], url=str(request.url))
        return reply(COW, cost=0.0031)

    vlm, spent, models = adapter(handler)
    result = asyncio.run(vlm.classify(b"png-bytes"))
    assert result.type == "creature" and models == ["google/gemini-3.8-flash"]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["temperature"] == 0 and seen["max_tokens"] == 700
    assert seen["response_format"]["json_schema"]["strict"] is True
    image = seen["messages"][1]["content"][0]["image_url"]["url"]
    assert image == "data:image/png;base64,cG5nLWJ5dGVz"
    assert spent == {"vlm_microusd:2026-11-16": 3100}
    assert vlm.prompt_version == "2"


def test_bad_json_is_retried_then_the_fallback_model_answers() -> None:
    def handler(request: httpx.Request, body: dict[str, Any], n: int) -> httpx.Response:
        if n == 1:
            return reply("this is not json")
        if n == 2:
            return reply({**COW, "type": "unicorn"})
        return reply("```json\n" + json.dumps(COW) + "\n```", cost=None)

    vlm, spent, models = adapter(handler)
    result = asyncio.run(vlm.classify(b"x"))
    assert result.label.en == "cow"
    assert models == [
        "google/gemini-3.8-flash",
        "google/gemini-3.8-flash",
        "qwen/qwen3-vl-235b-a22b-instruct",
    ]
    # Two reported costs plus one estimate.
    assert spent == {"vlm_microusd:2026-11-16": 2000 + 2000 + 4000}


def test_timeouts_and_outages_end_in_a_vlm_error_never_an_exception() -> None:
    def timeout(request: httpx.Request, body: dict[str, Any], n: int) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    vlm, spent, models = adapter(timeout)
    with pytest.raises(VlmError) as error:
        asyncio.run(vlm.classify(b"x"))
    assert error.value.code == "VLM_TIMEOUT" and len(models) == 3 and spent == {}

    vlm, spent, models = adapter(lambda request, body, n: httpx.Response(503))
    with pytest.raises(VlmError) as error:
        asyncio.run(vlm.classify(b"x"))
    assert error.value.code == "VLM_UNAVAILABLE" and len(models) == 3

    with pytest.raises(VlmError) as error:
        asyncio.run(UnavailableVlm().classify(b"x"))
    assert error.value.code == "VLM_UNAVAILABLE"


def test_daily_budget_stops_calls() -> None:
    vlm, spent, models = adapter(lambda request, body, n: reply(COW, cost=0.6), cap=1.0)
    asyncio.run(vlm.classify(b"x"))
    asyncio.run(vlm.classify(b"x"))
    with pytest.raises(VlmError) as error:
        asyncio.run(vlm.classify(b"x"))
    assert error.value.code == "VLM_UNAVAILABLE" and len(models) == 2
    assert vlm.budget.spent_usd() == 1.2


def test_fake_vlm_scripts_replies() -> None:
    fake = FakeVlm(VlmError("VLM_TIMEOUT"), COW)
    with pytest.raises(VlmError):
        asyncio.run(fake.classify(b"x"))
    assert asyncio.run(fake.classify(b"x")).type == "creature"
    assert asyncio.run(fake.classify(b"x")).type == "creature" and fake.calls == 3


def classify(changes: dict[str, Any], long_side: float = 120) -> Any:
    return postprocess(VlmResult.model_validate({**COW, **changes}), LABELS, long_side, SIZES, 0.6)


def test_postprocess_labels_parts_sizes_and_choices() -> None:
    cow = classify({})
    assert (cow.type, cow.size_class, cow.diet, cow.facing) == ("creature", "M", "plants", "left")
    assert cow.label == {"ru": "корова", "en": "cow"} and cow.choices == []
    assert len(cow.parts["legs"]) == 2 and cow.parts["tail"] is None

    # A known label decides the type, in either language, and can set other fields.
    wolf = classify({"label": {"ru": "Волк", "en": "wolf"}, "confidence": 0.4})
    assert wolf.type == "predator" and wolf.choices == []
    campfire = classify({"type": "doodle", "label": {"ru": "огонёк", "en": "Campfire"}})
    assert (campfire.type, campfire.variant) == ("fire", "campfire")

    assert [classify({}, side).size_class for side in (95, 96, 255, 256)] == ["S", "M", "M", "L"]

    # Boxes are clamped to the image; slivers under 2% of the area are dropped.
    assert clamp_box((-0.1, 0.5, 0.3, 0.9)) == pytest.approx((0.0, 0.5, 0.2, 0.5))
    assert clamp_box((0.5, 0.5, 0.1, 0.1)) is None
    messy = classify({"parts": {"legs": [[0.9, 0.9, 0.5, 0.5], [0.1, 0.1, 0.3, 0.3]], "head": [2, 2, 1, 1]}})
    assert len(messy.parts["legs"]) == 1 and messy.parts["head"] is None
    assert messy.parts["legs"][0] == pytest.approx((0.1, 0.1, 0.3, 0.3))

    unsure = classify({"confidence": 0.59, "top3": [{"type": "food", "p": 0.2}, {"type": "creature", "p": 0.5}]})
    assert unsure.choices == ["creature", "food", "doodle"]
    assert top_choices(VlmResult.model_validate({**COW, "type": "doodle", "top3": []})) == [
        "doodle", "creature", "food",
    ]  # fmt: skip
    assert classify({"confidence": 0.6}).choices == []


def test_postprocess_rejects_unsafe_and_multiple() -> None:
    for changes, code in [({"unsafe": True}, "UNSAFE_CONTENT"), ({"multiple": True}, "SELECTION_MULTIPLE")]:
        with pytest.raises(AppError) as error:
            classify(changes)
        assert error.value.code == code
