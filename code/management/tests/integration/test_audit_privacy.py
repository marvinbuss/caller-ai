"""Rejected sensitive configuration cannot partially persist into revisions or audit metadata."""

import asyncio
import json
from datetime import UTC, datetime

import pytest


def test_rejected_literal_credential_preserves_revision_and_safe_audit(
    configuration_graph, validator
):
    from management_app.adapters.audit_repository import LocalAuditRepository
    from management_app.adapters.configuration_repository import (
        ConfigurationRejected,
        LocalConfigurationRepository,
    )
    from management_app.services.audit import AuditRecord

    async def scenario():
        repository, audit = (
            LocalConfigurationRepository(validator=validator),
            LocalAuditRepository(),
        )
        before = await repository.initialize(configuration_graph, actor_id="manager-fixture")
        rejected = json.loads(json.dumps(configuration_graph))
        rejected["agents"][3]["prompt"] = "Bearer " + "synthetic-sensitive-candidate"
        with pytest.raises(ConfigurationRejected) as failure:
            await repository.publish(rejected, if_match=before[1], actor_id="manager-fixture")
        assert await repository.read_active() == before
        assert "synthetic-sensitive-candidate" not in str(failure.value)
        record = AuditRecord(
            id="audit-privacy-fixture",
            timestamp=datetime.now(UTC),
            action="authorization.check",
            outcome="denied",
            actor_ref="manager-fixture",
            revision_id=before[0]["id"],
            correlation_id="correlation-fixture",
            reason_code="permission_denied",
        )
        assert await audit.append(record)
        assert "synthetic-sensitive-candidate" not in json.dumps(audit.records)

    asyncio.run(scenario())


# --- live mode: no secret, token, key or input value in an error, a log event or a health body ---

PRIVATE_VALUE = "synthetic private value <that> must never appear"


def live_names():
    from tests.unit.test_live_settings import complete

    return [name for name in complete() if name != "CALLER_MODE"]


@pytest.mark.parametrize("name", live_names())
def test_a_bad_live_setting_stops_the_start_with_a_code_and_never_echoes_the_value(
    monkeypatch, name
):
    from management_app.settings import REASON_CODES, Settings, StartupError
    from tests.unit.test_live_settings import complete

    for key in complete():
        monkeypatch.delenv(key, raising=False)
    for key, value in {**complete(), name: PRIVATE_VALUE}.items():
        monkeypatch.setenv(key, value)
    try:
        Settings().live()
    except StartupError as error:
        assert error.code in REASON_CODES and str(error) == error.code
        assert PRIVATE_VALUE not in repr(error) and error.__cause__ is None
        assert error.__context__ is None
    else:
        # The sign-in secret is free text; it must still never appear in what the app reports.
        assert name == "CALLER_ENTRA_CLIENT_SECRET", "a bad value was accepted"


def test_live_composition_leaks_no_secret_into_health_events_logs_or_errors(monkeypatch, caplog):
    from management_app.health import lifecycle
    from management_app.settings import StartupError
    from tests.integration.test_live_composition import (
        PERMIT_PEM,
        compose,
        settings_for,
    )

    secret = "synthetic-sign-in-credential-that-must-never-appear"  # pragma: allowlist secret
    body = PERMIT_PEM.splitlines()[1]
    settings = settings_for(monkeypatch, {"CALLER_ENTRA_CLIENT_SECRET": secret})

    async def scenario():
        managed = await compose(settings)
        async with lifecycle(managed.api, settings):
            import httpx

            transport = httpx.ASGITransport(app=managed.api)
            async with httpx.AsyncClient(
                transport=transport, base_url="https://m.example"
            ) as client:
                texts = [
                    (await client.get("/healthz")).text,
                    (await client.get("/readyz")).text,
                    (
                        await client.get("/v2/configuration", headers={"Authorization": "Bearer x"})
                    ).text,
                    (await client.get("/auth/login")).text,
                ]
            events = json.dumps(managed.api.state.telemetry.events)
        return managed, texts, events

    managed, texts, events = asyncio.run(scenario())
    everything = "\n".join([*texts, events, repr(managed), repr(settings), repr(settings.live())])
    everything += caplog.text
    assert secret not in everything and body not in everything and "BEGIN" not in everything

    wrong = settings_for(monkeypatch, {"CALLER_REVIEWED_FINGERPRINT": "b" * 64})
    with pytest.raises(StartupError) as raised:
        asyncio.run(compose(wrong))
    assert secret not in repr(raised.value) and body not in repr(raised.value)
