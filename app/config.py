"""Environment settings (secrets, paths) and the YAML game config in config/."""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = Field(8000, ge=1, le=65535)
    data_dir: Path = Path("./data")
    # Empty means DATA_DIR/collabpad.db.
    db_path: str = ""
    config_dir: Path = Path(__file__).parent.parent / "config"
    public_url: str = "http://localhost:8000"
    vlm_api_key: str = ""
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    cors_origins: str = "http://localhost:5173"
    canvas_width: int = Field(1600, gt=0)
    canvas_height: int = Field(1000, gt=0)
    max_points_per_stroke: int = Field(2000, ge=2)
    max_payload_bytes: int = Field(65536, ge=1024)
    stroke_rate_limit: str = "30/10"
    brush_width_min: float = Field(1, gt=0)
    brush_width_max: float = Field(40, gt=0)
    simplify_epsilon: float = Field(0.5, ge=0)
    palette: str = "#000000,#FFFFFF,#E53935,#FB8C00,#FDD835,#43A047,#1E88E5,#8E24AA"
    static_dir: Path = Path(__file__).parent / "static"

    @model_validator(mode="after")
    def valid_settings(self) -> "Settings":
        if not self.db_path:
            self.db_path = str(self.data_dir / "collabpad.db")
        if self.brush_width_max < self.brush_width_min:
            raise ValueError("BRUSH_WIDTH_MAX must be >= BRUSH_WIDTH_MIN")
        if not re.fullmatch(r"[1-9]\d*/[1-9]\d*", self.stroke_rate_limit):
            raise ValueError("Rate limits must be positive count/seconds")
        if not self.colors or any(
            not re.fullmatch(r"#[0-9A-Fa-f]{6}", color) for color in self.colors
        ):
            raise ValueError("PALETTE must contain comma-separated #RRGGBB colors")
        return self

    @property
    def origins(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]

    @property
    def colors(self) -> list[str]:
        return [color.strip() for color in self.palette.split(",") if color.strip()]

    @property
    def sprites_dir(self) -> Path:
        return self.data_dir / "sprites"


class _Flag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class _Billing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recurring: bool


class Flags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ads: _Flag
    billing: _Billing
    vlm: _Flag
    sleep_catchup: bool


CONFIG_FILES = ("archetypes", "labels", "balance", "limits", "vlm", "billing")


class GameConfig:
    """Parsed config/*.yaml. Flags are typed; the rest stay plain dicts."""

    def __init__(self, directory: Path) -> None:
        def read(name: str) -> dict[str, Any]:
            data = yaml.safe_load((directory / f"{name}.yaml").read_text("utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"config/{name}.yaml must be a mapping")
            return data

        self.flags = Flags.model_validate(read("flags"))
        self.archetypes, self.labels, self.balance = (
            read("archetypes"),
            read("labels"),
            read("balance"),
        )
        self.limits, self.vlm, self.billing = read("limits"), read("vlm"), read("billing")
        chunk = self.limits["world"]["chunk_size"]
        for key in ("world", "private_world"):
            for side in ("bounds_w", "bounds_h"):
                value = self.limits[key][side]
                if value <= 0 or value % chunk:
                    raise ValueError(f"limits.{key}.{side} must be a multiple of {chunk}")
