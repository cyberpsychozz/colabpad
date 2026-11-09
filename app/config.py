"""Environment configuration. Comma-separated settings intentionally stay strings."""

import re
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = Field(8000, ge=1, le=65535)
    db_path: str = "./data/collabpad.db"
    cors_origins: str = "http://localhost:5173"
    canvas_width: int = Field(1600, gt=0)
    canvas_height: int = Field(1000, gt=0)
    max_strokes_per_canvas: int = Field(5000, gt=0)
    max_points_per_stroke: int = Field(2000, ge=2)
    max_payload_bytes: int = Field(65536, ge=1024)
    max_clients_per_canvas: int = Field(20, gt=0)
    stroke_rate_limit: str = "30/10"
    canvas_create_limit: str = "10/60"
    brush_width_min: float = Field(1, gt=0)
    brush_width_max: float = Field(40, gt=0)
    simplify_epsilon: float = Field(0.5, ge=0)
    name_max_len: int = Field(30, ge=1, le=30)
    palette: str = "#000000,#FFFFFF,#E53935,#FB8C00,#FDD835,#43A047,#1E88E5,#8E24AA"
    static_dir: Path = Path(__file__).parent / "static"

    @model_validator(mode="after")
    def valid_settings(self) -> "Settings":
        if self.brush_width_max < self.brush_width_min:
            raise ValueError("BRUSH_WIDTH_MAX must be >= BRUSH_WIDTH_MIN")
        for value in (self.stroke_rate_limit, self.canvas_create_limit):
            if not re.fullmatch(r"[1-9]\d*/[1-9]\d*", value):
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
