import os
from typing import TypedDict

import reflex as rx


class EndpointOverride(TypedDict, total=False):
    api_url: str


endpoint_override: EndpointOverride = {}
if "API_URL" in os.environ:
    endpoint_override["api_url"] = os.environ["API_URL"]

config = rx.Config(
    app_name="management_app",
    **endpoint_override,
    backend_port=8001,
    frontend_port=3000,
    backend_host="127.0.0.1",
    telemetry_enabled=False,
    frontend_packages=["react-debounce-input@3.3.0"],
    state_manager_mode=rx.constants.StateManagerMode.MEMORY,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(),
    ],
)
