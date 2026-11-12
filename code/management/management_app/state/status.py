"""Status page state: the management `GET /v1/status` answer, shaped for display."""

from collections.abc import AsyncIterator
from typing import Any

import reflex as rx
from fastapi import FastAPI
from reflex_base.registry import RegistrationContext

from management_app.state.editor_draft import EditorServices

Document = dict[str, Any]


def when(value: object) -> str:
    """`2026-11-11T10:00:00Z` as `2026-11-11 10:00:00 UTC`; nothing as `Never`."""
    if not isinstance(value, str) or not value:
        return "Never"
    return value[:19].replace("T", " ") + " UTC"


def reachable(value: object) -> str:
    return "Reachable" if value is True else "Unreachable"


def last_call(model: Document) -> str:
    outcome = model.get("lastCallOutcome")
    if outcome is None:
        return "No call yet"
    return f"{'Succeeded' if outcome == 'success' else 'Failed'} at {when(model.get('lastCallAt'))}"


def sections(body: Document) -> list[Document]:
    """One card per item; every value is a host, a name, a region, a state or a time."""
    runtime = body.get("runtime") or {}
    rows: list[Document] = [
        {
            "id": "status-runtime",
            "title": "Conversation service",
            "state": reachable(runtime.get("reachable")),
            "fields": [
                {"label": "Checked", "value": when(runtime.get("checkedAt"))},
            ]
            + (
                [{"label": "Last success", "value": when(runtime.get("lastSuccessAt"))}]
                if not runtime.get("reachable")
                else []
            ),
        }
    ]
    status = body.get("status")
    if not isinstance(status, dict):
        return rows
    text, voice = status["text"], status["voice"]
    rows.append(
        {
            "id": "status-text",
            "title": "Text model",
            "state": reachable(text.get("reachable")),
            "fields": [
                {"label": "Foundry service", "value": str(text.get("endpointHost", ""))},
                {"label": "Deployment", "value": str(text.get("deployment", ""))},
                {"label": "Checked", "value": when(text.get("checkedAt"))},
                {"label": "Last call", "value": last_call(text)},
            ],
        }
    )
    rows.append(
        {
            "id": "status-voice",
            "title": "Voice model",
            "state": reachable(voice.get("reachable")),
            "fields": [
                {"label": "Foundry service", "value": str(voice.get("endpointHost", ""))},
                {"label": "Region", "value": str(voice.get("region", ""))},
                {"label": "Deployment", "value": str(voice.get("model", ""))},
                {"label": "Checked", "value": when(voice.get("checkedAt"))},
                {"label": "Last call", "value": last_call(voice)},
            ],
        }
    )
    return rows


class StatusState(rx.State):
    loading: bool = False
    error: str = ""
    cards: list[Document] = []

    def _services(self) -> EditorServices:
        api = RegistrationContext.ensure_context().app.api_transformer
        services = getattr(getattr(api, "state", None), "editor_services", None)
        if not isinstance(api, FastAPI) or not isinstance(services, EditorServices):
            raise RuntimeError("status_unavailable")
        return services

    async def _fetch(self, refresh: bool) -> AsyncIterator[None]:
        self.loading, self.error = True, ""
        yield
        try:
            services = self._services()
            response = await services.request(
                self.router.session.session_id,
                self.router.session.client_token,
                "GET",
                path="/v1/status?refresh=1" if refresh else "/v1/status",
            )
            if response.status_code != 200:
                raise ValueError("status_unavailable")
            self.cards = sections(response.json())
        except Exception:
            self.cards = []
            self.error = "Status unavailable"
        finally:
            self.loading = False

    @rx.event
    async def load(self) -> AsyncIterator[None]:
        async for _update in self._fetch(False):
            yield

    @rx.event
    async def refresh(self) -> AsyncIterator[None]:
        async for _update in self._fetch(True):
            yield
