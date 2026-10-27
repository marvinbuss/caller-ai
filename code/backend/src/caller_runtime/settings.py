"""Frozen startup configuration; local mode never discovers live credentials."""

from typing import Literal, Self
from uuid import uuid4

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLER_",
        frozen=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    mode: Literal["local", "live"] = "local"
    instance_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[a-f0-9]{32}$")
    agents_sdk_tracing_enabled: bool = False
    realtime_tracing_enabled: bool = False
    trace_destination: Literal["memory"] = "memory"
    trace_include_content: bool = False

    @model_validator(mode="after")
    def validate_privacy(self) -> Self:
        if self.trace_include_content:
            raise ValueError("trace_content_forbidden")
        if self.realtime_tracing_enabled:
            raise ValueError("realtime_tracing_destination_unsupported")
        return self

    def validate_startup(self) -> None:
        if self.mode != "local":
            raise RuntimeError("live_mode_unavailable")
