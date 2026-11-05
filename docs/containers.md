# Foundation Containers

Both images have been built and exercised on Docker Desktop 29.9.2's Linux x86-64 engine.
Actual offline startup, published health/readiness, non-root execution, responsive Chromium
shell/event connection and owned-container cleanup pass. This is Linux-container evidence
on Windows, not a native Ubuntu or remote CI result.

Docker Desktop 29.9.2 is now available with a Linux x86-64 engine. The original runtime
candidate built, but its actual image scan identified high-severity `CVE-2026-85091` in
Debian zlib with no listed Debian fix. Both services now use the same Python 3.12.15 release
on digest-pinned Alpine 3.23. Actual runtime images and the management build stage pass
Docker Scout 1.27.0 with no detected vulnerabilities; no findings were suppressed.

Both Dockerfiles resolve Python 3.12.15 and uv 0.13.4 from official digest-pinned images.
Management also uses digest-pinned Node 24.22.0 during build. Digests were resolved from
official registries on 2026-10-23. Python dependencies use independent locks and the approved
Python feed; frontend packages use the approved npm feed with normal certificate validation.

Initial Bookworm candidates were rejected after editor image diagnostics reported nine high
vulnerabilities in Python and twelve in Node. Trixie still had the unfixed zlib issue.
The final images use Alpine, and only the Node executable is copied from its source image.
Bundled npm packages flagged in the Node source image are absent from the actual builder;
the complete builder, including installed frontend dependencies, was scanned separately.

Build-stage scanning identified `CVE-2026-112942` in Vite 8.3.2 and `CVE-2008-0888` in
standalone unzip. The frontend lock/override now selects Vite 8.3.3 through the approved
registry; BusyBox's existing unzip applet replaces the unnecessary standalone package.
Advisory data can change, so rerun the security gate rather than treating this result as permanent.

Build contexts are each service directory. Host environments, generated frontend output,
credentials, private keys, local test artifacts and Terraform state are excluded. Build stages
prepare non-editable runtime dependencies. Management builds the actual Reflex frontend,
using an absolute browser-visible API URL. Runtime stages omit uv, Node,
Bun and startup package-install commands, and run as UID/GID 10001.

Runtime listens internally on port 8000. Management's candidate entry point is the locked
Granian ASGI server on port 3000, with Reflex compilation skipped and compiled frontend
mounting enabled. This avoids the Reflex production CLI's rebuild path. One management
worker preserves the foundation's memory state; this is not a production scale-out design.

The image build argument `PUBLIC_API_URL` defaults to `http://localhost:3000`. Set it to
the actual browser-visible origin for another host port or HTTPS ingress. Relative URLs
fail the pinned framework's prerender step. Runtime environment changes cannot rewrite
compiled frontend URLs. The automated harness builds for its selected free host port.

```text
uv run --project code/tools --locked python code/tools/scripts/test_images.py
```

The harness builds actual images, verifies startup with `--network none`, then creates
loopback-published containers with all capabilities dropped and no-new-privileges. It checks
instance headers, the accepted schema, one-second health and the real browser shell at
360/768/1440 widths. A `finally` block removes only owned container IDs and temporary tags.
Docker output is decoded explicitly as UTF-8 for native Windows compatibility.

Actual runtime probing established that the pinned framework names its precompiled flags
`__REFLEX_SKIP_COMPILE` and `__REFLEX_MOUNT_FRONTEND_COMPILED_APP`; the unprefixed names
are ignored. Management uses an absolute `PUBLIC_API_URL` at build time because relative
framework endpoint URLs fail prerendering. Vite is overridden to 8.3.3 for
`CVE-2026-112942`; the vulnerable standalone unzip package is unnecessary because the
base supplies BusyBox's unzip applet. Rebuild and scan the real builder and runtime after
dependency changes rather than inferring safety from a base tag alone.

Run only on a Docker-capable host with Linux containers:

```text
docker build --tag caller-runtime:local code/backend
docker build --tag caller-management:local code/management
docker run --rm --env CALLER_MODE=local --publish 127.0.0.1:8000:8000 caller-runtime:local
docker run --rm --env CALLER_MODE=local --publish 127.0.0.1:3000:3000 caller-management:local
```

Use separate terminals for the two foreground runs. Require actual health/readiness checks,
the real browser shell and event connection, non-root execution, no startup downloads, and
guaranteed cleanup before accepting either image. Do not publish images or provision Azure
resources as part of this credential-free foundation.

## Published images

The two images are published by the `images` workflow to GitHub's container registry as
`ghcr.io/marvinbuss/caller-ai/runtime` and `ghcr.io/marvinbuss/caller-ai/management`, with the immutable tags
`sha-<40 hex>` and `v<semver>`. They are scanned for secrets and HIGH or CRITICAL vulnerabilities before
publishing and are pulled without a credential. Nothing environment-specific is built into an image; a deployed
app receives its settings at start (see [Application live mode](runbooks/application-live-mode.md)). Publishing
rules are in [Image publishing](runbooks/image-publishing.md).
