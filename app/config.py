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
    # process: the world runs in its own process. inline: stepped by tests. off: no world.
    sim_mode: str = Field("process", pattern="^(process|inline|off)$")
    # How often the simulation reports its tick times (logged by main); the load test lowers it.
    sim_stats_interval_s: float = Field(60, gt=0)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_from: str = ""
    smtp_user: str = ""
    smtp_password: str = ""
    # Yandex Advertising Network (RSYa) block ids; ads also need flags.ads.enabled.
    ads_block_side: str = ""
    ads_block_bottom: str = ""
    vlm_api_key: str = ""
    # HTTP proxy for the VLM calls only (e.g. a Tailscale exit-node proxy). Empty: direct.
    vlm_proxy_url: str = Field("", pattern=r"^(https?://\S+)?$")
    # Robokassa shop identifier and the TEST passwords #1 and #2 from its technical settings.
    robokassa_merchant_login: str = ""
    robokassa_test_password1: str = ""
    robokassa_test_password2: str = ""
    # The signature hash chosen in the shop's technical settings.
    robokassa_hash: str = Field("md5", pattern="^(md5|sha1|sha256|sha384|sha512)$")
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000"
    )
    max_points_per_stroke: int = Field(2000, ge=2)
    max_payload_bytes: int = Field(65536, ge=1024)
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


class _Seller(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    inn: str = Field("", pattern=r"^(\d{12})?$")


class Legal(BaseModel):
    """config/legal.yaml: the seller's public details. Empty strings mean "not filled in yet"."""

    model_config = ConfigDict(extra="forbid")
    seller: _Seller = _Seller()
    email: str = Field("", pattern=r"^([^@\s]+@[^@\s]+\.[^@\s]+)?$")
    phone: str = Field("", pattern=r"^(\+?[\d\s()-]{6,20})?$")


CONFIG_FILES = ("archetypes", "labels", "balance", "limits", "vlm", "billing", "legal")


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
        self.legal = Legal.model_validate(read("legal"))
        # Served as-is at /ads.txt (the IAB list of authorized ad sellers); optional.
        ads_txt = directory / "ads.txt"
        self.ads_txt = ads_txt.read_text("utf-8") if ads_txt.is_file() else None
        chunk = self.limits["world"]["chunk_size"]
        for key in ("world", "private_world"):
            for side in ("bounds_w", "bounds_h"):
                value = self.limits[key][side]
                if value <= 0 or value % chunk:
                    raise ValueError(f"limits.{key}.{side} must be a multiple of {chunk}")
