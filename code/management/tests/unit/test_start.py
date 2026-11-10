"""The start script points the exported frontend at the address the deployment is reached on."""

import gzip
from pathlib import Path

import pytest

from management_app.start import apply, rewrite, socket_origin

BAKED = "http://caller-api-origin.invalid"


def export(root: Path) -> Path:
    (root / "assets").mkdir(parents=True)
    script = root / "assets" / "env.js"
    text = f"var e={{PING:`{BAKED}/ping`,EVENT:`ws://caller-api-origin.invalid/_event`}};"
    script.write_text(text, encoding="utf-8")
    script.with_name("env.js.gz").write_bytes(gzip.compress(text.encode(), mtime=0))
    (root / "assets" / "other.js").write_text("var untouched=1;", encoding="utf-8")
    (root / "assets" / "logo.png").write_bytes(b"\x89PNG caller-api-origin.invalid")
    return script


def test_the_baked_http_and_socket_addresses_follow_the_start_value(tmp_path: Path) -> None:
    script = export(tmp_path)
    assert rewrite(tmp_path, BAKED, "https://app.example.net") == 1
    text = script.read_text(encoding="utf-8")
    assert "https://app.example.net/ping" in text and "wss://app.example.net/_event" in text
    assert "caller-api-origin.invalid" not in text
    assert gzip.decompress(script.with_name("env.js.gz").read_bytes()).decode() == text
    assert (tmp_path / "assets" / "other.js").read_text() == "var untouched=1;"
    assert b"caller-api-origin.invalid" in (tmp_path / "assets" / "logo.png").read_bytes()


def test_a_port_and_a_plain_http_address_are_kept(tmp_path: Path) -> None:
    script = export(tmp_path)
    rewrite(tmp_path, BAKED, "http://127.0.0.1:3999")
    assert "ws://127.0.0.1:3999/_event" in script.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "target",
    [
        "https://x.example.net/<script>",
        "javascript:alert(1)",
        "https://x.example.net/",
        "https://x.example.net/path",
        "ftp://x.example.net",
        "https://user@x.example.net",
        "https://x.example.net`;alert(1);`",
        "",
    ],
)
def test_only_a_plain_origin_is_accepted(tmp_path: Path, target: str) -> None:
    script = export(tmp_path)
    before = script.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_frontend_origin"):
        rewrite(tmp_path, BAKED, target)
    assert script.read_text(encoding="utf-8") == before


def test_apply_changes_nothing_without_a_start_value_or_when_it_equals_the_baked_one(
    tmp_path: Path,
) -> None:
    script = export(tmp_path)
    baked = tmp_path / "baked-api-url"
    baked.write_text(BAKED, encoding="utf-8")
    before = script.read_text(encoding="utf-8")
    assert apply(tmp_path, baked, target="") == 0
    assert apply(tmp_path, baked, target=BAKED) == 0
    assert script.read_text(encoding="utf-8") == before
    assert apply(tmp_path, baked, target="https://app.example.net") == 1


def test_the_environment_supplies_the_start_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = export(tmp_path)
    baked = tmp_path / "baked-api-url"
    baked.write_text(BAKED, encoding="utf-8")
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.setenv("CALLER_PUBLIC_ORIGIN", "https://public.example.net")
    assert apply(tmp_path, baked) == 1
    assert "wss://public.example.net/_event" in script.read_text(encoding="utf-8")


def test_an_api_url_equal_to_the_baked_placeholder_falls_back_to_the_public_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = export(tmp_path)
    baked = tmp_path / "baked-api-url"
    baked.write_text(BAKED, encoding="utf-8")
    # The image sets API_URL to the placeholder it was built with.
    monkeypatch.setenv("API_URL", BAKED)
    monkeypatch.setenv("CALLER_PUBLIC_ORIGIN", "https://public.example.net")
    assert apply(tmp_path, baked) == 1
    text = script.read_text(encoding="utf-8")
    assert "wss://public.example.net/_event" in text and "caller-api-origin.invalid" not in text


def test_socket_origin_maps_the_scheme() -> None:
    assert socket_origin("https://a.example.net") == "wss://a.example.net"
    assert socket_origin("http://a.example.net") == "ws://a.example.net"
