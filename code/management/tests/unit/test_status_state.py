"""Status page shaping: every shown value is a host, a name, a region, a state or a time."""

import json

from management_app.state.status import last_call, sections, when
from tests.contract.test_status_api import RUNTIME_BODY


def test_sections_show_runtime_text_and_voice() -> None:
    cards = sections(
        {
            "runtime": {"reachable": True, "checkedAt": "2026-11-11T10:00:05Z"},
            "status": RUNTIME_BODY,
        }
    )
    assert [card["id"] for card in cards] == ["status-runtime", "status-text", "status-voice"]
    assert [card["state"] for card in cards] == ["Reachable", "Reachable", "Unreachable"]
    text = {field["label"]: field["value"] for field in cards[1]["fields"]}
    assert text == {
        "Foundry service": "caller-int-foundry.openai.azure.com",
        "Deployment": "gpt-6-1-sol",
        "Checked": "2026-11-11 10:00:00 UTC",
        "Last call": "Succeeded at 2026-11-11 09:58:12 UTC",
    }
    voice = {field["label"]: field["value"] for field in cards[2]["fields"]}
    assert voice["Region"] == "swedencentral"
    assert voice["Deployment"] == "gpt-live-1"
    assert voice["Last call"] == "No call yet"
    shown = json.dumps(cards).lower()
    for secret in ("token", "key", "secret", "password", "https://"):
        assert secret not in shown


def test_unreachable_runtime_shows_the_last_success_only() -> None:
    cards = sections(
        {
            "runtime": {
                "reachable": False,
                "checkedAt": "2026-11-11T10:00:00Z",
                "lastSuccessAt": "2026-11-11T09:40:00Z",
            },
            "status": None,
        }
    )
    assert len(cards) == 1
    assert cards[0]["state"] == "Unreachable"
    assert {"label": "Last success", "value": "2026-11-11 09:40:00 UTC"} in cards[0]["fields"]
    never = sections({"runtime": {"reachable": False, "checkedAt": "2026-11-11T10:00:00Z"}})
    assert {"label": "Last success", "value": "Never"} in never[0]["fields"]


def test_times_and_calls_are_plain_text() -> None:
    assert when(None) == "Never"
    assert last_call({"lastCallOutcome": "failure", "lastCallAt": "2026-11-11T09:00:00Z"}) == (
        "Failed at 2026-11-11 09:00:00 UTC"
    )
