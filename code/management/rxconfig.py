import os
from typing import TypedDict, Unpack

import reflex as rx
from reflex_base.plugins.base import Plugin, PreCompileContext

PREVIEW_WARMUP = """
function callerPreviewWarmup() {
    return {
        name: "caller-preview-warmup",
        async configurePreviewServer(server) {
            const { resolve } = await import("node:path");
            const { pathToFileURL } = await import("node:url");
            await import(pathToFileURL(resolve(server.config.root, "build/server/index.js")).href);
        },
    };
}
"""


def initialize_preview_module(content: str) -> str:
    """Initialize SSR modules before the first timed prerender request, not within it."""
    if 'name: "caller-preview-warmup"' in content:
        return content
    declaration = "export default defineConfig"
    plugins = "alwaysUseReactDomServerNode(),"
    if declaration not in content or plugins not in content:
        raise RuntimeError("frontend_preview_template_changed")
    return content.replace(declaration, PREVIEW_WARMUP + "\n" + declaration, 1).replace(
        plugins, "callerPreviewWarmup(),\n    " + plugins, 1
    )


class PreviewWarmupPlugin(Plugin):
    """Keep cold SSR module loading inside build startup rather than page response budgets."""

    def pre_compile(self, **context: Unpack[PreCompileContext]) -> None:
        context["add_modify_task"]("vite.config.js", initialize_preview_module)


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
    show_built_with_reflex=False,
    frontend_packages=["react-debounce-input@3.3.0", "@xyflow/react@12.12.0"],
    state_manager_mode=rx.constants.StateManagerMode.MEMORY,
    plugins=[
        PreviewWarmupPlugin(),
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                appearance="light", accent_color="teal", gray_color="gray", radius="small"
            )
        ),
    ],
)
