"""Contracts for the feature 007 int probes: preview, closed result lines and synthetic texts."""

import json
import socket
from pathlib import Path

import pytest

PROBES = Path(__file__).resolve().parents[1] / "scripts" / "probes"
NAMES = ("live_instructions_append", "reasoning_minimal", "replay_foreign_items")
TEXT_FIELDS = {"content", "instructions", "input", "description", "arguments", "output"}


@pytest.fixture
def probes(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBES))
    import common
    import live_instructions_append
    import reasoning_minimal
    import replay_foreign_items

    return {
        "common": common,
        "live_instructions_append": live_instructions_append,
        "reasoning_minimal": reasoning_minimal,
        "replay_foreign_items": replay_foreign_items,
    }


def texts(document, found=None):
    found = [] if found is None else found
    if isinstance(document, dict):
        for key, value in document.items():
            if key in TEXT_FIELDS and isinstance(value, str):
                found.append(value)
            else:
                texts(value, found)
    elif isinstance(document, list):
        for item in document:
            texts(item, found)
    return found


@pytest.mark.quick
@pytest.mark.parametrize("name", NAMES)
def test_preview_makes_no_network_call_and_prints_closed_lines(probes, name, monkeypatch, capsys):
    def refuse(*_arguments, **_options):
        raise AssertionError("network_call_in_preview")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(probes["common"], "credential", refuse)
    monkeypatch.setattr(probes["common"], "settings", refuse)
    assert probes[name].main(["--preview"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines
    for line in lines:
        document = json.loads(line)
        assert set(document) == {"probe", "run", "outcome", "value"}
        assert document["outcome"] == "planned"
        assert set(document["value"]) == {"call"}


@pytest.mark.quick
def test_results_refuse_free_text_and_unknown_outcomes(probes):
    common = probes["common"]
    lines = []
    common.result("P1", 0, "passed", {"cut": False, "acknowledged": True}, lines.append)
    common.result("P2", "total", "summary", {"reasoning_effort_default": "minimal"}, lines.append)
    assert [set(json.loads(line)) for line in lines] == [{"probe", "run", "outcome", "value"}] * 2
    for value in (
        {"text": "The provider said something."},
        {"code": common.LONG_COMMENTARY},
        {"Nested Key": 1},
        {"items": [1, 2]},
    ):
        with pytest.raises(ValueError):
            common.result("P1", 0, "passed", value, lines.append)
    with pytest.raises(ValueError):
        common.result("P1", 0, "provider text", {}, lines.append)
    with pytest.raises(ValueError):
        common.result("P9", 0, "passed", {}, lines.append)


@pytest.mark.quick
def test_failure_codes_never_carry_provider_messages(probes):
    common = probes["common"]

    class StatusError(Exception):
        status_code = 400

    assert common.failure_code(StatusError("secret provider text")) == "http_400"
    assert common.failure_code(TimeoutError("details")) == "timeouterror"
    assert common.failure_code(RuntimeError("live_not_started")) == "live_not_started"
    assert common.failure_code(RuntimeError("Some provider message")) == "runtimeerror"
    error = probes["live_instructions_append"].provider_error
    assert str(error({"error": {"code": "invalid_value", "message": "x"}})) == (
        "live_error.invalid_value"
    )
    assert str(error({"error": {"code": "Has spaces"}})) == "live_provider_error"


@pytest.mark.quick
def test_only_a_400_counts_as_a_refused_payload(probes):
    common = probes["common"]

    def status(code):
        error = Exception()
        error.status_code = code
        return error

    assert common.refused(status(400))
    assert not any(common.refused(status(code)) for code in (401, 403, 404, 429, 500))
    assert not common.refused(TimeoutError())


@pytest.mark.quick
def test_every_probe_text_is_a_synthetic_constant(probes):
    common = probes["common"]
    p1, p2, p3 = (probes[name] for name in NAMES)
    payloads = [
        p1.commentary_event(),
        p1.update_event(3),
        p2.request("deployment", "minimal"),
        p2.request("deployment", "low"),
        p3.request("deployment"),
    ]
    found = [text for payload in payloads for text in texts(payload)]
    assert found
    assert set(found) <= common.SYNTHETIC_TEXTS
    start = p1.start_event("shipped instructions", "marin")
    assert texts(start) == ["shipped instructions"]
    assert start["session"]["delegation"] == {"type": "client"}
    assert all(payload.get("store") is False for payload in payloads[2:])


@pytest.mark.quick
def test_live_appends_fit_the_adapter_limit_and_the_update_contract(probes):
    common, p1 = probes["common"], probes["live_instructions_append"]
    for text in (common.LONG_COMMENTARY, common.DELEGATION_UPDATE):
        assert len(text.encode("utf-8")) <= 500
    update = p1.update_event(2)
    assert update["type"] == "session.instructions.append"
    assert update["delegation_id"] is None
    assert update["event_id"] == "probe-update-2"
    assert update["content"].startswith(
        "Delegation update. This replaces all earlier delegation updates.\nBackend tools:\n"
    )
    assert "Generate <|SpawnThinking|>" in update["content"]


@pytest.mark.quick
def test_p1_counts_a_run_as_cut_only_when_most_of_the_commentary_was_not_spoken(probes):
    p1 = probes["live_instructions_append"]
    full = len(probes["common"].LONG_COMMENTARY)
    kept = p1.judge(10.0, [9.0, 10.5, 30.2], full, 24.0, True)
    assert kept == {
        "cut": False,
        "acknowledged": True,
        "spoken_ratio": 1.0,
        "audio_seconds": 24.0,
        "transcript_after_update_seconds": 20.2,
    }
    cut = p1.judge(10.0, [9.0, 10.5], full // 4, 3.0, False)
    assert cut["cut"] is True and cut["spoken_ratio"] == 0.25
    assert p1.judge(10.0, [], 0, 0.0, True)["transcript_after_update_seconds"] == 0.0


@pytest.mark.quick
def test_settings_accept_only_azure_ai_origins(probes):
    common = probes["common"]
    values = {
        "CALLER_LIVE_ENDPOINT": "https://live.services.ai.azure.com/",
        "CALLER_LIVE_VOICE": "marin",
        "CALLER_TEXT_ENDPOINT": "https://text.openai.azure.com",
        "CALLER_TEXT_DEPLOYMENT": "gpt-6.1-sol",
    }
    settings = common.settings(values)
    assert settings.live_endpoint == "https://live.services.ai.azure.com"
    assert settings.text_deployment == "gpt-6.1-sol"
    for name, bad in (
        ("CALLER_LIVE_ENDPOINT", "http://live.services.ai.azure.com"),
        ("CALLER_TEXT_ENDPOINT", "https://example.com"),
        ("CALLER_TEXT_ENDPOINT", "https://text.openai.azure.com/path"),
        ("CALLER_TEXT_DEPLOYMENT", ""),
    ):
        with pytest.raises(SystemExit):
            common.settings(values | {name: bad})
