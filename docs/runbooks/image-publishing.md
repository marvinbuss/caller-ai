# Image publishing

Both applications run from public images in GitHub's container registry. This guide covers tags, immutability,
the scans, making the packages public, rolling back and the retention rule.

| Image | Source | Address |
|-------|--------|---------|
| Runtime | `code/backend/Dockerfile` | `ghcr.io/marvinbuss/caller-ai/runtime` |
| Management | `code/management/Dockerfile` | `ghcr.io/marvinbuss/caller-ai/management` |

Nothing environment-specific is built into an image. Environments differ only in the settings the deployment
gives the containers (see [Application live mode](application-live-mode.md)).

## Tags and immutability

Every published image has the immutable tag `sha-<40 hex>` (the commit) and, for a release tag `v<major>.<minor>.<patch>`,
the tag `v<semver>`. A tag is published once and never overwritten. Before building, the workflow checks that
the tag is free with `verify_published_images.py --operation tag-free`; an existing tag stops the run.

## The workflow

The [images workflow](../../.github/workflows/images.yml) runs on a push to `main`, on a `v*` tag and manually.
For each image it:

1. checks that the tags are free;
2. builds the image from the repository's Dockerfile;
3. scans the built image with the digest-pinned Trivy for secrets and HIGH or CRITICAL vulnerabilities; a
   finding stops the run before anything is published;
4. signs in to the registry with the workflow token (`packages: write`, nothing else) and pushes the tags;
5. proves the image can be pulled **without credentials** with `--operation anonymous-pull`;
6. prints the digest of every tag in the job summary (`--operation digest`).

All actions are pinned by commit. The workflow has no delete or prune step.

## First publication

New packages are private. After the first run, open each package's settings in GitHub and set its visibility
to public, then rerun the workflow: the anonymous pull check fails until the package is public. Making a
package public exposes the image content, so only the scanned application images belong here and the owner
approves the decision once (see [Security](../security.md)).

## Use a tag in an environment

Copy the `sha-<40 hex>` tag of each image into `app_environment.container_apps` in the environment's
`<env>.tfvars.json`, validate the files, and run `terraform-app-environment`. A digest may be used instead of a
tag. The environment check's `images-running` result confirms that each app runs exactly the configured image
and names the app and version when a pull failed.

## Roll back

Set the previous tag again and apply. Because tags are never deleted or overwritten, every earlier image
remains available.

## Retention

Published tags are never deleted or overwritten, and the workflow has no step that deletes or prunes package
versions. A test enforces that no such step exists. If storage or a vulnerable old image becomes a concern,
supersede it with a new tag; removal is a manual maintainer decision outside this workflow.
