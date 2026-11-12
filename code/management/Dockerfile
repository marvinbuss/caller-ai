FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv
FROM node:24.22.0-alpine3.23@sha256:e44c87c7dd212720eb00469aeac89504ca2be77b8f0d6dbffb30665d5fcc59e1 AS node
FROM python:3.12.15-alpine3.23@sha256:3d0ca66b723a0ff2a7a6e424cc2d01443e1b7c75ffcc110145f75d0f4daf6d63 AS build
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apk add --no-cache bash 'curl>=8.23.0-r0' 'libcurl>=8.23.0-r0' libstdc++ libatomic
RUN apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'
ARG PUBLIC_API_URL=http://caller-api-origin.invalid
ENV UV_DEFAULT_INDEX=https://pypi.org/simple \
    NPM_CONFIG_REGISTRY=https://registry.npmjs.org/ \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    API_URL=${PUBLIC_API_URL}
WORKDIR /app
COPY pyproject.toml uv.lock .python-version rxconfig.py .npmrc bunfig.toml ./
COPY management_app ./management_app
COPY reflex.lock ./reflex.lock
COPY assets ./assets
RUN uv sync --locked --no-dev --no-editable
RUN uv run --locked --no-dev reflex init --no-agents
RUN CALLER_FRONTEND_EXPORT=1 uv run --locked --no-dev reflex export --frontend-only --no-zip
RUN printf '%s' "$PUBLIC_API_URL" > /app/baked-api-url

FROM python:3.12.15-alpine3.23@sha256:3d0ca66b723a0ff2a7a6e424cc2d01443e1b7c75ffcc110145f75d0f4daf6d63
ARG PUBLIC_API_URL=http://caller-api-origin.invalid
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CALLER_MODE=local \
    API_URL=${PUBLIC_API_URL} \
    REFLEX_ENV_MODE=prod \
    __REFLEX_SKIP_COMPILE=true \
    __REFLEX_MOUNT_FRONTEND_COMPILED_APP=true
# The pinned base still ships krb5-libs 1.22.1-r0 (HIGH CVEs); drop once the base is rebuilt.
RUN apk add --no-cache --upgrade 'krb5-libs>=1.22.3-r0'
RUN addgroup -S -g 10001 caller && adduser -S -u 10001 -G caller -h /home/caller caller
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
COPY --from=build --chown=10001:10001 /app/management_app /app/management_app
COPY --from=build --chown=10001:10001 /app/rxconfig.py /app/rxconfig.py
COPY --from=build --chown=10001:10001 /app/baked-api-url /app/baked-api-url
COPY --from=build --chown=10001:10001 /app/assets /app/assets
COPY --from=build --chown=10001:10001 /app/.web/build /app/.web/build
COPY --from=build --chown=10001:10001 /app/.web/routes.json /app/.web/routes.json
COPY --from=build --chown=10001:10001 /app/.web/reflex.json /app/.web/reflex.json
USER 10001:10001
EXPOSE 3000
CMD ["/app/.venv/bin/python", "-m", "management_app.start"]
