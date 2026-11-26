"""ALIVE providers: classification, drawing parts, and text-only choreography.

The model sits behind `Vlm`. `OpenRouterVlm` talks to an OpenRouter-compatible
API; `FakeVlm` is the scripted stand-in for tests; `UnavailableVlm` is used when
the flag is off or no key is configured. A failure is never a dead end: the
ordinary classification caller turns VlmError into the manual type grid. Text
animation failures keep the original strokes and return an editable error.
"""

import base64
import json
import logging
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.alive.choreography import DrawingParts, Motion, PartsError, validate_parts

logger = logging.getLogger(__name__)

TYPES = (
    "creature", "predator", "aquatic", "person", "food", "plant", "water",
    "house", "explosive", "vehicle", "remote", "fire", "sky", "doodle",
)  # fmt: skip
TypeName = Literal[
    "creature", "predator", "aquatic", "person", "food", "plant", "water",
    "house", "explosive", "vehicle", "remote", "fire", "sky", "doodle",
]  # fmt: skip
Box = tuple[float, float, float, float]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Guess(_Model):
    type: TypeName
    p: float = Field(ge=0, le=1)


class Label(_Model):
    ru: str = Field(min_length=1, max_length=40)
    en: str = Field(min_length=1, max_length=40)


class Parts(_Model):
    legs: list[Box] = []
    wings: list[Box] = []
    wheels: list[Box] = []
    head: Box | None = None
    tail: Box | None = None


class VlmResult(_Model):
    type: TypeName
    confidence: float = Field(ge=0, le=1)
    top3: list[Guess] = Field(default_factory=list, max_length=3)
    label: Label
    locomotion: Literal["walk", "fly", "swim", "hop", "none"] = "none"
    diet: Literal["plants", "meat", "omni"] | None = None
    role: Literal["villager", "warrior", "fisherman", "medic"] | None = None
    variant: (
        Literal[
            "fruit_tree", "pasture", "campfire", "cloud", "sun", "boat", "car", "plane"
        ]
        | None
    ) = None
    facing: Literal["left", "right"] = "right"
    multiple: bool = False
    unsafe: bool = False
    parts: Parts = Field(default_factory=Parts)

    @field_validator("diet", "role", "variant", mode="before")
    @classmethod
    def null_string(cls, value: Any) -> Any:
        # Models sometimes spell null as a string.
        return None if value in ("null", "none", "") else value


class VlmError(Exception):
    """Typed timeout, availability or animation-contract failure."""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


class Vlm(Protocol):
    prompt_version: str

    generation_prompt_version: str
    parts_prompt_version: str

    async def classify(self, image_png: bytes) -> VlmResult: ...

    async def identify_parts(self, image_png: bytes) -> DrawingParts: ...

    async def animate(self, drawing: DrawingParts, description: str) -> Motion: ...


class UnavailableVlm:
    prompt_version = "none"
    generation_prompt_version = "none"
    parts_prompt_version = "none"

    async def identify_parts(self, image_png: bytes) -> DrawingParts:
        raise VlmError("VLM_UNAVAILABLE", "disabled or not configured")

    async def animate(self, drawing: DrawingParts, description: str) -> Motion:
        raise VlmError("VLM_UNAVAILABLE", "disabled or not configured")

    async def classify(self, image_png: bytes) -> VlmResult:
        raise VlmError("VLM_UNAVAILABLE", "disabled or not configured")


class FakeVlm:
    """Returns scripted replies in order: VlmResult, dict or Exception; the last repeats."""

    prompt_version = "fake"
    generation_prompt_version = "fake-1"
    parts_prompt_version = "fake-parts-1"

    def _scripted(self, schema):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        try:
            return (
                reply
                if isinstance(reply, schema)
                else schema.model_validate(reply)
            )
        except ValidationError as exc:
            raise VlmError("ANIMATION_INVALID") from exc

    async def identify_parts(self, image_png: bytes) -> DrawingParts:
        return self._scripted(DrawingParts)

    async def animate(self, drawing: DrawingParts, description: str) -> Motion:
        return self._scripted(Motion)

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies) or [VlmError("VLM_UNAVAILABLE", "nothing scripted")]
        self.calls = 0

    async def classify(self, image_png: bytes) -> VlmResult:
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, VlmResult) else VlmResult.model_validate(reply)


def load_prompt(path: Path) -> tuple[str, str]:
    """(version, prompt text) from a file whose first line is `version: N`."""
    header, _, body = path.read_text("utf-8").partition("\n")
    if not header.startswith("version:"):
        raise ValueError(f"{path} must start with a 'version:' line")
    return header.split(":", 1)[1].strip(), body.strip()


def response_schema(model: str, contract: type[BaseModel]) -> dict[str, Any]:
    """Keep Gemini's decoding grammar small; validate full limits after parsing."""
    schema = contract.model_json_schema()
    if model.startswith("google/gemini-") and contract is Motion:
        def simplify(node):
            if isinstance(node, dict):
                # Large nested array maxima multiply Gemini's grammar states.
                # This is a fresh wire schema, not the server validation model.
                if node.get("type") == "array":
                    node.pop("maxItems", None)
                for child in node.values():
                    simplify(child)
            elif isinstance(node, list):
                for child in node:
                    simplify(child)

        simplify(schema)
    return schema


class Budget:
    """Daily spend cap in USD, persisted through two callbacks (the metric table)."""

    def __init__(
        self,
        cap_usd: float,
        read: Callable[[str], int],
        add: Callable[[str, int], None],
        today: Callable[[], str] = lambda: datetime.now(UTC).strftime("%Y-%m-%d"),
    ) -> None:
        self.cap_usd, self.read, self.add, self.today = cap_usd, read, add, today

    def key(self) -> str:
        return f"vlm_microusd:{self.today()}"

    def spent_usd(self) -> float:
        return self.read(self.key()) / 1_000_000

    def exhausted(self) -> bool:
        return self.spent_usd() >= self.cap_usd

    def charge(self, usd: float) -> None:
        self.add(self.key(), max(1, round(usd * 1_000_000)))


class OpenRouterVlm:
    def __init__(
        self,
        client: httpx.AsyncClient,
        config: dict[str, Any],
        api_key: str,
        prompt: tuple[str, str],
        budget: Budget,
        generation_prompt: tuple[str, str] | None = None,
    ) -> None:
        self.client, self.config, self.api_key, self.budget = (
            client,
            config,
            api_key,
            budget,
        )
        self.prompt_version, self.prompt = prompt
        self.generation_prompt_version, self.generation_prompt = (
            generation_prompt
            or load_prompt(
                Path(__file__).resolve().parents[2] / "prompts" / "generate.md"
            )
        )
        self.parts_prompt_version, self.parts_prompt = load_prompt(
            Path(__file__).resolve().parents[2] / "prompts" / "parts.md"
        )

    async def classify(self, image_png: bytes) -> VlmResult:
        return await self._operation(image_png=image_png)

    async def identify_parts(self, image_png: bytes) -> DrawingParts:
        return await self._operation(image_png=image_png, operation="parts")

    async def animate(self, drawing: DrawingParts, description: str) -> Motion:
        return await self._operation(operation="motion", drawing=drawing, description=description)

    async def _operation(
        self,
        *,
        image_png: bytes | None = None,
        operation: Literal["classify", "parts", "motion"] = "classify",
        drawing: DrawingParts | None = None,
        description: str | None = None,
    ):
        """All operations share budget and retries; the pipeline owns one deadline."""
        primary, fallback = self.config["primary_model"], self.config["fallback_model"]
        if operation == "motion":
            primary = self.config.get("motion_model", primary)
            fallback = self.config.get("motion_fallback_model", fallback)
        attempts = [primary] * (1 + self.config["retries"]) + [fallback]
        last = VlmError("VLM_UNAVAILABLE", "no attempt made")
        skip_primary = False
        for model in attempts:
            if skip_primary and model == primary:
                continue  # Timeouts and rejected requests go directly to the fallback.
            if self.budget.exhausted():
                raise VlmError("VLM_UNAVAILABLE", "daily budget reached")
            try:
                result = await self._ask(model, operation, image_png, drawing, description)
                if operation == "parts" and result.status == "ok" and result.reason is None:
                    # Coordinate relationships are not expressible in the wire schema.
                    # Check them inside the existing bounded retry/budget loop too.
                    validate_parts(result.parts, self.config.get("generation", {}).get("parts", 16))
                return result
            except httpx.TimeoutException:
                last = VlmError("VLM_TIMEOUT", model)
                skip_primary = True
            except (
                httpx.HTTPError,
                ValidationError,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
                AttributeError,
            ) as exc:
                if (isinstance(exc, httpx.HTTPStatusError)
                        and 400 <= exc.response.status_code < 500
                        and exc.response.status_code not in (408, 429)):
                    skip_primary = True
                last = VlmError(
                    "ANIMATION_INVALID"
                    if operation != "classify"
                    and isinstance(
                        exc,
                        (
                            ValidationError,
                            ValueError,
                            KeyError,
                            TypeError,
                            IndexError,
                            AttributeError,
                        ),
                    )
                    else "VLM_UNAVAILABLE",
                    f"{model}: {self.failure_reason(exc)}",
                )
            logger.warning("VLM attempt failed: operation=%s %s", operation, last)
        raise last

    def failure_reason(self, exc: Exception) -> str:
        """Log actionable metadata without echoing drawings, requests or keys."""
        if isinstance(exc, PartsError):
            return str(exc)
        if isinstance(exc, ValidationError):
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            return "schema " + "; ".join(
                f"{'.'.join(map(str, error['loc']))}: {error['type']}" for error in errors[:5]
            )
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            message = response.reason_phrase
            try:
                body = response.json()
                error = body.get('error') if isinstance(body, dict) else None
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    message = error['message']
                    metadata = error.get('metadata')
                    if isinstance(metadata, dict):
                        raw = metadata.get('raw')
                        try:
                            raw = json.loads(raw) if isinstance(raw, str) else raw
                        except ValueError:
                            raw = None
                        detail = raw.get('error', raw) if isinstance(raw, dict) else None
                        if isinstance(detail, dict) and isinstance(detail.get('message'), str):
                            message += ': ' + detail['message']
            except ValueError:
                pass
            message = message.replace(self.api_key, '[redacted]') if self.api_key else message
            return f"HTTP {response.status_code}: {' '.join(message.split())[:300]}"
        if isinstance(exc, json.JSONDecodeError):
            return f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        return type(exc).__name__

    async def _ask(self, model, operation, image_png, drawing, description):
        schema, prompt, name = {
            "classify": (VlmResult, self.prompt, "drawing"),
            "parts": (DrawingParts, self.parts_prompt, "drawing_parts_v2"),
            "motion": (Motion, self.generation_prompt, "motion_v1"),
        }[operation]
        if operation == "motion":
            content = [
                {"type": "text", "text": json.dumps({"drawing": drawing.model_dump()}, ensure_ascii=False)},
                {"type": "text", "text": description},
            ]
        else:
            content = [{
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")},
            }]
        response = await self.client.post(
            f"{self.config['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=(self.config["timeout_s"] if operation == "classify"
                     else self.config.get("generation", {}).get("request_timeout_s", 30)),
            json={
                **({"provider": {"require_parameters": True}} if operation != "classify" else {}),
                "model": model,
                "temperature": self.config["temperature"],
                "max_tokens": self.config.get("generation_max_tokens", 8192)
                if operation != "classify"
                else self.config["max_tokens"],
                "usage": {"include": True},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": name,
                        "strict": True,
                        "schema": response_schema(model, schema),
                    },
                },
                "messages": [
                    {
                        "role": "system",
                        "content": prompt,
                    },
                    {"role": "user", "content": content},
                ],
            },
        )
        # Any answer from the provider costs money, whether or not it parses.
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                self.budget.charge(self.config["estimated_call_usd"])
                raise
            usage = body.get("usage") if isinstance(body, dict) else None
            cost = usage.get("cost") if isinstance(usage, dict) else None
            self.budget.charge(
                cost
                if isinstance(cost, int | float) and math.isfinite(cost) and cost >= 0
                else self.config["estimated_call_usd"]
            )
        else:
            body = {}
        response.raise_for_status()
        text = body["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return schema.model_validate(json.loads(text))
