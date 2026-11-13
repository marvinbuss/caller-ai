"""Settings page state: its own draft holding only `conversationSettings`.

Publishing sends the loaded active revision with only `conversationSettings` replaced, under that
revision's ETag, so unpublished graph edits never travel with it. A conflict keeps the draft and
names the newer revision. The voice catalog, defaults and limits come from the runtime status; if
the runtime cannot be reached the values are shown read-only.
"""

import copy
from collections.abc import AsyncIterator
from typing import Any, cast

import reflex as rx
from fastapi import FastAPI
from reflex.event import EventHandler
from reflex_base.registry import RegistrationContext

from management_app.api.configuration import CONFIRMATION_HEADER
from management_app.services import default_prompts
from management_app.services.configuration_validation import GRAPH_FIELDS
from management_app.state.editor_draft import EditorServices

Document = dict[str, Any]
IDLE_RANGE = (30, 120)


def voice_label(voice: Document, default: str) -> str:
    """`Quartz — English (Australian), feminine`, with `(default)` on the default voice."""
    label = f"{voice['name']} \u2014 {voice['language']}"
    if voice.get("accent"):
        label += f" ({voice['accent']})"
    if voice.get("presentation"):
        label += f", {str(voice['presentation']).lower()}"
    if voice["id"] == default:
        label += " (default)"
    return label


def voice_groups(voices: list[Document], default: str) -> list[Document]:
    """The catalog grouped by language, in catalog order."""
    groups: dict[str, list[dict[str, str]]] = {}
    for voice in voices:
        groups.setdefault(str(voice["language"]), []).append(
            {"id": str(voice["id"]), "label": voice_label(voice, default)}
        )
    return [{"language": language, "voices": rows} for language, rows in groups.items()]


def check(values: dict[str, str], voices: set[str], max_tokens: int) -> dict[str, str]:
    """Field errors naming the allowed range; empty when every value is allowed."""
    errors: dict[str, str] = {}
    if values["voice"] not in voices:
        errors["voice"] = "Choose a voice from the list."
    for field, low, high in (
        ("maxOutputTokens", 1, max_tokens),
        ("idleTimeoutSeconds", *IDLE_RANGE),
    ):
        text = values[field].strip()
        if not text.isdigit() or not low <= int(text) <= high:
            errors[field] = f"Integer, {low} to {high}."
    return errors


def chosen(values: dict[str, str], defaults: Document) -> Document:
    """`conversationSettings` with only the values that differ from the environment defaults."""
    settings: Document = {}
    if values["voice"] != defaults["voice"]:
        settings["voice"] = values["voice"]
    for field in ("maxOutputTokens", "idleTimeoutSeconds"):
        if int(values[field]) != int(defaults[field]):
            settings[field] = int(values[field])
    return settings


# The editable 2.2.0 texts in page order: (field, section, label).
TEXT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("voiceInstructions", "Voice", "Voice conversation instructions"),
    ("voicePlatformRules", "Voice", "Voice platform rules"),
    ("voiceGlobalPrompt", "Voice", "Voice global prompt"),
    ("textPlatformRules", "Text", "Text platform rules"),
    ("textGlobalPrompt", "Text", "Text global prompt"),
)


def text_overrides(stored: Document) -> dict[str, str | None]:
    """The stored value of each text, or `None` when the shipped default applies."""
    return {
        field: stored[field] if isinstance(stored.get(field), str) else None
        for field, _section, _label in TEXT_FIELDS
    }


def shown_texts(overrides: dict[str, str | None]) -> dict[str, str]:
    """What each text box shows: the stored value, or the shipped default."""
    return {
        field: value if value is not None else str(default_prompts.SHIPPED[field])
        for field, value in overrides.items()
    }


def text_errors(overrides: dict[str, str | None]) -> dict[str, str]:
    """Byte limit errors for the texts that would be stored."""
    errors: dict[str, str] = {}
    for field, value in overrides.items():
        if value is None:
            continue
        low, high = default_prompts.TEXT_LIMITS[field]
        size = len(value.encode("utf-8"))
        if size > high or size < low or (low and not value.strip()):
            errors[field] = f"{low} to {high} bytes." if low else f"At most {high} bytes."
    return errors


def publishable(overrides: dict[str, str | None], stored: Document) -> Document:
    """The stored settings with the texts replaced; a restored text is removed (absent)."""
    kept = {key: value for key, value in stored.items() if key not in dict(overrides)}
    return kept | {field: value for field, value in overrides.items() if value is not None}


def stored_quiet(stored: Document) -> bool | None:
    """The stored `quietHandover`, or `None` when absent (the default, on)."""
    value = stored.get("quietHandover")
    return value if isinstance(value, bool) else None


def with_quiet(settings: Document, quiet: bool | None) -> Document:
    """Settings with `quietHandover` stored, or removed when it is back to the default."""
    rest = {key: value for key, value in settings.items() if key != "quietHandover"}
    return rest if quiet is None else rest | {"quietHandover": quiet}


def needs_confirmation(overrides: dict[str, str | None], stored: Document) -> bool:
    """Platform rules or voice instructions differ from the active revision (FR-030, FR-037)."""
    return any(
        overrides.get(field) != (stored.get(field) if isinstance(stored.get(field), str) else None)
        for field in default_prompts.CONFIRMED_SETTINGS
    )


class SettingsState(rx.State):
    loading: bool = False
    error: str = ""
    read_only: bool = True
    busy: bool = False
    voice: str = ""
    max_tokens: str = ""
    idle: str = ""
    field_errors: dict[str, str] = {}
    message: str = ""
    conflict: str = ""
    groups: list[Document] = []
    max_tokens_limit: int = 0
    environment: list[dict[str, str]] = []
    # Unpublished edits; a page load keeps them and their starting revision.
    dirty: bool = False
    notice_revision: int = 0
    # The modality texts as shown, and whether each one is the shipped default.
    texts: dict[str, str] = {}
    defaulted: dict[str, bool] = {}
    confirm_open: bool = False
    # The quiet update on handover: shown on when the stored value is absent (FR-017).
    quiet_handover: bool = True
    quiet_default: bool = True
    _quiet: bool | None = None
    _overrides: dict[str, str | None] = {}
    _revision: Document = {}
    _etag: str = ""
    _defaults: Document = {}

    def _services(self) -> EditorServices:
        api = RegistrationContext.ensure_context().app.api_transformer
        services = getattr(getattr(api, "state", None), "editor_services", None)
        if not isinstance(api, FastAPI) or not isinstance(services, EditorServices):
            raise RuntimeError("settings_unavailable")
        return services

    async def _get(self, path: str) -> Any:
        return await self._services().request(
            self.router.session.session_id, self.router.session.client_token, "GET", path=path
        )

    @rx.var
    def tokens_note(self) -> str:
        limit = self.max_tokens_limit or "the service limit"
        return (
            f"Integer, 1 to {limit}. Applies to text replies and delegated work, "
            "not to spoken output."
        )

    @rx.var
    def text_bytes(self) -> dict[str, str]:
        """`<used> of <limit> bytes` for each text box."""
        return {
            field: f"{len(value.encode('utf-8'))} of {default_prompts.TEXT_LIMITS[field][1]} bytes"
            for field, value in self.texts.items()
            if field in default_prompts.TEXT_LIMITS
        }

    def _show_texts(self) -> None:
        self.texts = shown_texts(self._overrides)
        self.defaulted = {field: value is None for field, value in self._overrides.items()}
        self.quiet_handover = self._quiet is not False
        self.quiet_default = self._quiet is None

    @rx.event
    async def load(self) -> AsyncIterator[None]:
        self.loading, self.error, self.message, self.conflict = True, "", "", ""
        self.field_errors = {}
        yield
        try:
            keep = self.dirty and bool(self._revision)
            revision = await self._get("/v2/configuration")
            if revision.status_code != 200:
                raise ValueError("configuration_unavailable")
            status = await self._get("/v1/status")
            if status.status_code != 200:
                raise ValueError("status_unavailable")
            if not keep:
                self._revision = revision.json()
                self._etag = revision.headers["ETag"]
                self.notice_revision = 0
            runtime = status.json().get("status")
            current = self._revision.get("conversationSettings") or {}
            if isinstance(runtime, dict):
                defaults = runtime["defaults"]
                self.read_only = False
                self.groups = voice_groups(runtime["supportedVoices"], defaults["voice"])
                self.max_tokens_limit = int(runtime["limits"]["maxOutputTokens"])
                self.environment = [
                    {"label": "Foundry service", "value": runtime["text"]["endpointHost"]},
                    {"label": "Text model deployment", "value": runtime["text"]["deployment"]},
                    {"label": "Voice model deployment", "value": runtime["voice"]["model"]},
                    {"label": "Voice region", "value": runtime["voice"]["region"]},
                ]
            else:
                defaults = {"voice": "", "maxOutputTokens": "", "idleTimeoutSeconds": ""}
                self.read_only = True
                self.groups, self.environment, self.max_tokens_limit = [], [], 0
            self._defaults = dict(defaults)
            if not keep:
                self.voice = str(current.get("voice", defaults["voice"]))
                self.max_tokens = str(current.get("maxOutputTokens", defaults["maxOutputTokens"]))
                self.idle = str(current.get("idleTimeoutSeconds", defaults["idleTimeoutSeconds"]))
                self._overrides = text_overrides(current)
                self._quiet = stored_quiet(current)
            self._show_texts()
        except Exception:
            self._revision, self._etag, self.dirty = {}, "", False
            self.error = "Settings unavailable"
        finally:
            self.loading = False

    @rx.event
    def set_voice(self, value: str) -> None:
        self.voice, self.message, self.dirty = value, "", True

    @rx.event
    def set_max_tokens(self, value: str) -> None:
        self.max_tokens, self.message, self.dirty = value, "", True

    @rx.event
    def set_idle(self, value: str) -> None:
        self.idle, self.message, self.dirty = value, "", True

    @rx.event
    def set_text(self, field: str, value: str) -> None:
        if field not in self._overrides:
            return
        self._overrides = self._overrides | {field: value}
        self._show_texts()
        self.message, self.dirty = "", True

    @rx.event
    def restore_text(self, field: str) -> None:
        """Back to the shipped default: the field is removed from the published settings."""
        if field not in self._overrides:
            return
        self._overrides = self._overrides | {field: None}
        self._show_texts()
        self.message, self.dirty = "", True

    @rx.event
    def set_quiet_handover(self, value: bool) -> None:
        self._quiet = bool(value)
        self._show_texts()
        self.message, self.dirty = "", True

    @rx.event
    def restore_quiet_handover(self) -> None:
        self._quiet = None
        self._show_texts()
        self.message, self.dirty = "", True

    @rx.event
    def cancel_confirmation(self) -> None:
        self.confirm_open = False

    @rx.event
    async def publish_confirmed(self) -> AsyncIterator[None]:
        self.confirm_open = False
        async for _update in self._publish(confirmed=True):
            yield

    @rx.event
    async def publish(self) -> AsyncIterator[None]:
        async for _update in self._publish(confirmed=False):
            yield

    async def _publish(self, *, confirmed: bool) -> AsyncIterator[None]:
        if self.busy or self.read_only or not self._revision:
            return
        values = {
            "voice": self.voice,
            "maxOutputTokens": self.max_tokens,
            "idleTimeoutSeconds": self.idle,
        }
        voices = {row["id"] for group in self.groups for row in group["voices"]}
        self.field_errors = check(values, voices, self.max_tokens_limit) | text_errors(
            self._overrides
        )
        self.message, self.conflict = "", ""
        if self.field_errors:
            return
        stored = self._revision.get("conversationSettings") or {}
        if not confirmed and needs_confirmation(self._overrides, stored):
            # "This can break voice or text behavior. Publish anyway?"
            self.confirm_open = True
            return
        graph = {
            key: copy.deepcopy(value)
            for key, value in self._revision.items()
            if key in GRAPH_FIELDS and key != "conversationSettings"
        }
        # The server writes 2.2.0 only when a 2.2.0 field remains; otherwise the stored version.
        graph["schemaVersion"] = self._revision.get("schemaVersion", "2.1.0")
        others = {
            key: value
            for key, value in stored.items()
            if key not in {"voice", "maxOutputTokens", "idleTimeoutSeconds"}
        }
        settings = with_quiet(
            chosen(values, self._defaults) | publishable(self._overrides, others), self._quiet
        )
        if settings:
            graph["conversationSettings"] = settings
        self.busy = True
        yield
        try:
            response = await self._services().request(
                self.router.session.session_id,
                self.router.session.client_token,
                "PUT",
                graph=graph,
                etag=self._etag,
                headers={CONFIRMATION_HEADER: "confirmed"} if confirmed else None,
            )
            if response.status_code == 200:
                self._revision = response.json()
                self._etag = response.headers["ETag"]
                self.dirty, self.notice_revision = False, 0
                self._overrides = text_overrides(self._revision.get("conversationSettings") or {})
                self._quiet = stored_quiet(self._revision.get("conversationSettings") or {})
                self._show_texts()
                self.message = (
                    f"Published revision {self._revision.get('revisionNumber', '')}. "
                    "The settings apply to conversations started from now on; "
                    "running conversations keep their values."
                )
            elif response.status_code == 409:
                current = await self._get("/v2/configuration")
                number = current.json().get("revisionNumber", "") if current.is_success else ""
                self.conflict = (
                    f"Revision {number} was published meanwhile. "
                    "Your settings are kept; reload to publish them on top of it."
                )
            else:
                refused = dict(self.field_errors)
                for item in response.json().get("errors", []):
                    field = str(item.get("path", "")).rpartition(".")[2]
                    if field in values or field in self._overrides:
                        refused[field] = (
                            "This text is too long."
                            if item.get("code") == "configuration_text_too_large"
                            else "This value is not allowed."
                        )
                self.field_errors = refused
                self.message = "Publishing refused."
        except Exception:
            self.message = "Publishing unavailable. Your settings are kept."
        finally:
            self.busy = False

    @rx.event
    async def check_revision(self) -> None:
        """Background check for a newer publication; the settings draft stays as it is."""
        if not self._revision:
            return
        try:
            response = await self._get("/v2/configuration")
            if response.status_code != 200:
                return
            number = int(response.json().get("revisionNumber", 0))
            if response.headers.get("ETag") != self._etag and number > int(
                self._revision.get("revisionNumber", 0)
            ):
                self.notice_revision = number
        except Exception:
            return

    @rx.event
    async def reload(self) -> AsyncIterator[None]:
        """Take the newer revision: keep unpublished settings, refresh unchanged ones."""
        self.notice_revision = 0
        if self.dirty:
            await cast(EventHandler, SettingsState.rebase).fn(self)
            return
        async for _update in cast(EventHandler, SettingsState.load).fn(self):
            yield

    @rx.event
    async def rebase(self) -> None:
        """Take the newer active revision as the base and keep the settings being edited."""
        try:
            revision = await self._get("/v2/configuration")
            if revision.status_code != 200:
                raise ValueError("configuration_unavailable")
            self._revision, self._etag = revision.json(), revision.headers["ETag"]
            self.conflict, self.notice_revision = "", 0
        except Exception:
            self.message = "Reload unavailable. Your settings are kept."
