FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv
FROM python:3.12.15-alpine3.23@sha256:3d0ca66b723a0ff2a7a6e424cc2d01443e1b7c75ffcc110145f75d0f4daf6d63 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_DEFAULT_INDEX=https://pypi.org/simple \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.15-alpine3.23@sha256:3d0ca66b723a0ff2a7a6e424cc2d01443e1b7c75ffcc110145f75d0f4daf6d63
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CALLER_MODE=local \
    OPENAI_AGENTS_DISABLE_TRACING=1
RUN addgroup -S -g 10001 caller && adduser -S -u 10001 -G caller -h /home/caller caller
WORKDIR /app
COPY --from=build --chown=10001:10001 /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "caller_runtime.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio", "--ws-max-size", "65536"]
