"""Checks published application images in GitHub Container Registry (FR-036, FR-037).

Operations: `tag-free` fails when the tag already exists (tags are never overwritten),
`anonymous-pull` proves the manifest can be fetched without credentials, and `digest` prints the
manifest digest. The registry is only read; nothing is deleted or modified.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

REGISTRY = "https://ghcr.io"
IMAGE = re.compile(r"ghcr\.io/marvinbuss/caller-ai/(runtime|management)")
TAG = re.compile(r"sha-[0-9a-f]{40}|v[0-9]+\.[0-9]+\.[0-9]+")
ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


def repository(image: str, tag: str) -> str:
    match = IMAGE.fullmatch(image)
    if match is None or TAG.fullmatch(tag) is None:
        raise ValueError("published_image_reference_invalid")
    return image.removeprefix("ghcr.io/")


def token(client: httpx.Client, name: str, *, authenticated: bool) -> str:
    auth = None
    if authenticated and os.environ.get("GITHUB_TOKEN"):
        auth = (os.environ.get("GITHUB_ACTOR", "github-actions"), os.environ["GITHUB_TOKEN"])
    response = client.get(
        f"{REGISTRY}/token",
        params={"service": "ghcr.io", "scope": f"repository:{name}:pull"},
        auth=auth,
    )
    response.raise_for_status()
    value = response.json().get("token")
    if not isinstance(value, str) or not value:
        raise ValueError("published_image_token_invalid")
    return value


def manifest(client: httpx.Client, name: str, tag: str, *, authenticated: bool) -> httpx.Response:
    bearer = token(client, name, authenticated=authenticated)
    return client.get(
        f"{REGISTRY}/v2/{name}/manifests/{tag}",
        headers={"Authorization": f"Bearer {bearer}", "Accept": ACCEPT},
    )


def tag_free(client: httpx.Client, image: str, tag: str) -> dict[str, str]:
    name = repository(image, tag)
    response = manifest(client, name, tag, authenticated=True)
    if response.status_code == 200:
        raise ValueError("published_image_tag_exists")
    if response.status_code not in {401, 404}:
        raise ValueError("published_image_lookup_failed")
    return {"result": "tag_free", "image": image, "tag": tag}


def anonymous_pull(client: httpx.Client, image: str, tag: str) -> dict[str, str]:
    name = repository(image, tag)
    response = manifest(client, name, tag, authenticated=False)
    if response.status_code != 200:
        raise ValueError("published_image_not_anonymously_pullable")
    return {"result": "anonymous_pull_ok", "image": image, "tag": tag}


def digest(client: httpx.Client, image: str, tag: str) -> dict[str, str]:
    name = repository(image, tag)
    response = manifest(client, name, tag, authenticated=False)
    value = response.headers.get("Docker-Content-Digest", "")
    if response.status_code != 200 or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("published_image_digest_unavailable")
    return {"result": "digest", "image": image, "tag": tag, "digest": value}


OPERATIONS = {"tag-free": tag_free, "anonymous-pull": anonymous_pull, "digest": digest}


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation", choices=tuple(OPERATIONS), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    arguments = parser.parse_args(argv)
    try:
        owned = client is None
        http = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        try:
            report = OPERATIONS[arguments.operation](http, arguments.image, arguments.tag)
        finally:
            if owned:
                http.close()
    except (ValueError, httpx.HTTPError) as error:
        message = str(error) if isinstance(error, ValueError) else "published_image_lookup_failed"
        print(json.dumps({"result": "published_image_check_failed", "reason": message}))
        return 2
    print(json.dumps(report, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and arguments.operation == "digest":
        with Path(summary).open("a", encoding="utf-8") as target:
            target.write(f"- `{report['image']}:{report['tag']}` digest `{report['digest']}`\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
