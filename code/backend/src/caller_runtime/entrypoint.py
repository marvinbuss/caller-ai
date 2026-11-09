"""Entrypoint for the container: local mode keeps the health-only app, live mode composes it."""

from fastapi import FastAPI

from caller_runtime.live import build_live_runtime
from caller_runtime.main import create_app
from caller_runtime.settings import Settings


def create_app_from_environment() -> FastAPI:
    """Return the app for the environment's `CALLER_MODE`; a refused live start raises one code."""
    settings = Settings()
    if settings.mode != "live":
        return create_app(settings)
    composition = build_live_runtime(settings)
    return create_app(settings, dependencies=composition.dependencies, runtime=composition.runtime)
