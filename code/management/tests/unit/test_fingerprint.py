"""The fingerprint tool prints what the app will compute for the same non-secret settings."""

import json
from pathlib import Path

import pytest

from management_app.fingerprint import fingerprint_of, main
from management_app.live import reviewed_fingerprint
from management_app.settings import Settings, StartupError
from tests.unit.test_live_settings import complete

SECRETS = ("CALLER_ENTRA_CLIENT_SECRET", "CALLER_SIGNING_KEY_PEM")


def reviewable() -> dict[str, str]:
    values = {name: value for name, value in complete().items() if name not in SECRETS}
    for name in ("CALLER_REVIEW_REFERENCE", "CALLER_REVIEWED_FINGERPRINT"):
        values.pop(name)
    return values


def test_the_tool_prints_the_value_the_app_computes(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in complete():
        monkeypatch.delenv(name, raising=False)
    for name, value in complete().items():
        monkeypatch.setenv(name, value)
    expected = reviewed_fingerprint(Settings().live())
    assert fingerprint_of(reviewable()) == expected


def test_secrets_and_the_reviewed_value_in_the_input_change_nothing() -> None:
    changed = {**complete(), "CALLER_ENTRA_CLIENT_SECRET": "other"}  # pragma: allowlist secret
    assert fingerprint_of(changed) == fingerprint_of(reviewable())


def test_main_reads_a_settings_file_and_prints_only_the_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(reviewable()), encoding="utf-8")
    assert main(["--settings", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == fingerprint_of(reviewable()) and captured.err == ""


@pytest.mark.parametrize("name", ["CALLER_TENANT_ID", "CALLER_COSMOS_ENDPOINT", "AZURE_CLIENT_ID"])
def test_a_missing_setting_gives_one_fixed_code_and_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    values = reviewable()
    del values[name]
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    assert main(["--settings", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "settings_missing" and captured.out == ""


def test_unknown_arguments_and_bad_files_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--other"]) == 2
    assert main(["--settings", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().out == ""


def test_an_invalid_value_never_appears_in_the_error() -> None:
    values = {**reviewable(), "CALLER_TENANT_ID": "synthetic-not-a-guid"}
    with pytest.raises(StartupError) as raised:
        fingerprint_of(values)
    assert raised.value.code == "settings_invalid"
    assert "synthetic-not-a-guid" not in str(raised.value)
