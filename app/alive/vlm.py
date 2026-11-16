"""ALIVE step 5: classify a drawing with a vision-language model.

The model sits behind `Vlm`. `OpenRouterVlm` talks to an OpenRouter-compatible
API; `FakeVlm` is the scripted stand-in for tests; `UnavailableVlm` is used when
the flag is off or no key is configured. A failure is never a dead end: the
caller turns VlmError into the manual type grid.
"""

import base64
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
        Literal["fruit_tree", "pasture", "campfire", "cloud", "sun", "boat", "car", "plane"]
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
    """code is VLM_TIMEOUT or VLM_UNAVAILABLE."""

    def __init__(self, code: str, reason: str = "") -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code


class Vlm(Protocol):
    prompt_version: str

    async def classify(self, image_png: bytes) -> VlmResult: ...


class UnavailableVlm:
    prompt_version = "none"

    async def classify(self, image_png: bytes) -> VlmResult:
        raise VlmError("VLM_UNAVAILABLE", "disabled or not configured")


class FakeVlm:
    """Returns scripted replies in order: VlmResult, dict or Exception; the last repeats."""

    prompt_version = "fake"

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
    ) -> None:
        self.client, self.config, self.api_key, self.budget = client, config, api_key, budget
        self.prompt_version, self.prompt = prompt

    async def classify(self, image_png: bytes) -> VlmResult:
        """Primary model with retries, then the fallback model once."""
        attempts = [self.config["primary_model"]] * (1 + self.config["retries"])
        attempts.append(self.config["fallback_model"])
        last = VlmError("VLM_UNAVAILABLE", "no attempt made")
        for model in attempts:
            if self.budget.exhausted():
                raise VlmError("VLM_UNAVAILABLE", "daily budget reached")
            try:
                return await self._ask(model, image_png)
            except httpx.TimeoutException:
                last = VlmError("VLM_TIMEOUT", model)
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError) as exc:
                last = VlmError("VLM_UNAVAILABLE", f"{model}: {type(exc).__name__}")
            logger.warning("VLM attempt failed: %s", last)
        raise last

    async def _ask(self, model: str, image_png: bytes) -> VlmResult:
        image = base64.b64encode(image_png).decode("ascii")
        response = await self.client.post(
            f"{self.config['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.config["timeout_s"],
            json={
                "model": model,
                "temperature": self.config["temperature"],
                "max_tokens": self.config["max_tokens"],
                "usage": {"include": True},
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "drawing",
                        "strict": True,
                        "schema": VlmResult.model_json_schema(),
                    },
                },
                "messages": [
                    {"role": "system", "content": self.prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{image}"},
                            }
                        ],
                    },
                ],
            },
        )
        # Any answer from the provider costs money, whether or not it parses.
        body = response.json() if response.status_code == 200 else {}
        cost = (body.get("usage") or {}).get("cost")
        if response.status_code == 200:
            self.budget.charge(
                cost if isinstance(cost, int | float) else self.config["estimated_call_usd"]
            )
        response.raise_for_status()
        text = body["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return VlmResult.model_validate(json.loads(text))
