"""The shared `application/problem+json` error response of the management APIs.

Every API names its own fixed error codes; this builds the one response shape (no-store, correlation
id, `WWW-Authenticate` on 401) so no response can leak detail beyond status, code and correlation.
"""

from http import HTTPStatus
from typing import Any

from starlette.responses import JSONResponse


def problem_response(
    status: int,
    correlation: str,
    code: str,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "code": code,
        "correlation_id": correlation,
    }
    if errors:
        body["errors"] = errors
    headers = {"Cache-Control": "no-store", "X-Correlation-ID": correlation}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        body, status_code=status, headers=headers, media_type="application/problem+json"
    )
