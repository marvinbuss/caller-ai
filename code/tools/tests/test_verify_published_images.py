"""Contracts for the published-image checks, using a fake registry."""

from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
IMAGE = "ghcr.io/marvinbuss/caller-ai/runtime"
TAG = "sha-" + "a" * 40
DIGEST = "sha256:" + "b" * 64


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import verify_published_images

    return verify_published_images


def registry(manifest_status=200, seen=None, public=True):
    def respond(request):
        if seen is not None:
            seen.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "synthetic-token"})
        if "/manifests/" in request.url.path:
            authorized = request.headers.get("Authorization") == "Bearer synthetic-token"
            status = manifest_status if authorized and (public or True) else 401
            return httpx.Response(status, headers={"Docker-Content-Digest": DIGEST})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(respond))


def test_tag_free_fails_when_the_tag_exists_and_passes_when_it_does_not(module):
    with pytest.raises(ValueError, match="published_image_tag_exists"):
        module.tag_free(registry(200), IMAGE, TAG)
    assert module.tag_free(registry(404), IMAGE, TAG)["result"] == "tag_free"


def test_tag_free_fails_on_an_unexpected_registry_answer(module):
    with pytest.raises(ValueError, match="published_image_lookup_failed"):
        module.tag_free(registry(500), IMAGE, TAG)


def test_anonymous_pull_sends_no_credentials(module, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-credential")
    seen = []
    assert module.anonymous_pull(registry(200, seen), IMAGE, TAG)["result"] == "anonymous_pull_ok"
    token_request = next(request for request in seen if request.url.path == "/token")
    assert "authorization" not in token_request.headers
    with pytest.raises(ValueError, match="published_image_not_anonymously_pullable"):
        module.anonymous_pull(registry(404), IMAGE, TAG)


def test_digest_returns_the_manifest_digest(module):
    assert module.digest(registry(200), IMAGE, TAG)["digest"] == DIGEST
    with pytest.raises(ValueError, match="published_image_digest_unavailable"):
        module.digest(registry(404), IMAGE, TAG)


@pytest.mark.parametrize(
    "image,tag",
    [
        ("ghcr.io/other/runtime", TAG),
        ("ghcr.io/marvinbuss/caller-ai/runtime/../x", TAG),
        (IMAGE, "latest"),
        (IMAGE, "v1.2"),
    ],
)
def test_only_the_project_images_and_immutable_tags_are_accepted(module, image, tag):
    with pytest.raises(ValueError, match="published_image_reference_invalid"):
        module.digest(registry(200), image, tag)


def test_main_reports_failures_without_a_traceback(module, capsys):
    code = module.main(
        ["--operation", "tag-free", "--image", IMAGE, "--tag", TAG], client=registry(200)
    )
    assert code == 2 and "published_image_tag_exists" in capsys.readouterr().out


def test_the_images_workflow_never_deletes_or_prunes_package_versions():
    import yaml

    path = SCRIPTS.parents[2] / ".github/workflows/images.yml"
    source = path.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    for forbidden in ("delete-package-versions", "-X DELETE", "--method DELETE", "prune"):
        assert forbidden not in source
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "tag-free" in source and "anonymous-pull" in source and "--operation digest" in source
