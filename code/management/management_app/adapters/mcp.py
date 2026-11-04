"""Executes one reviewed MCP tool call over the pinned, DNS-validated public transport.

The tool list is paged (at most 4 pages of 256) and the reviewed action must match exactly one
advertised tool; an unlisted, duplicated or paginated-away action fails with
`reviewed_mcp_action_unavailable`. What a server advertises never grants authority: the call runs
only after the capability policy authorized it. Server log output is discarded and results are
decoded under the same size and shape limits as plain public reads.
"""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from management_app.adapters.public_tools import PublicReadTransport, decode_result
from management_app.services.capability_policy import AuthorizedCapability
from management_app.services.configuration_validation import encoded


class McpTransport(PublicReadTransport):
    async def execute(
        self, selected: AuthorizedCapability, *, request_id: str, deadline: float
    ) -> dict[str, Any]:
        if selected.connection_id is None:
            raise RuntimeError("reviewed_mcp_connection_required")
        logger = logging.getLogger("mcp")
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.propagate = False

        async def discard_server_log(*args: Any, **kwargs: Any) -> None:
            return None

        try:
            async with asyncio.timeout_at(deadline):
                async with self.pinned_client(selected, deadline) as client:
                    async with streamable_http_client(selected.destination, http_client=client) as (
                        reader,
                        writer,
                        _session_id,
                    ):
                        remaining = deadline - asyncio.get_running_loop().time()
                        async with ClientSession(
                            reader,
                            writer,
                            read_timeout_seconds=timedelta(seconds=max(0.001, remaining)),
                            logging_callback=discard_server_log,
                        ) as session:
                            await session.initialize()
                            cursor = None
                            matches = 0
                            seen: set[str] = set()
                            for _page in range(4):
                                catalog = await session.list_tools(cursor=cursor)
                                if len(catalog.tools) > 256:
                                    raise ValueError("oversized_mcp_catalog")
                                matches += sum(
                                    tool.name == selected.action_id for tool in catalog.tools
                                )
                                cursor = catalog.nextCursor
                                if cursor is None:
                                    break
                                if cursor in seen:
                                    raise ValueError("invalid_mcp_pagination")
                                seen.add(cursor)
                            if cursor is not None or matches != 1:
                                raise ValueError("reviewed_mcp_action_unavailable")
                            result = await session.call_tool(
                                selected.action_id,
                                arguments=selected.inputs,
                                meta={"requestId": request_id},
                            )
                            if result.isError:
                                raise ValueError("mcp_action_failed")
                            if result.structuredContent is not None:
                                return decode_result(encoded(result.structuredContent))
                            if len(result.content) == 1 and result.content[0].type == "text":
                                return decode_result(result.content[0].text.encode("utf-8"))
                            raise ValueError("unsupported_mcp_result")
        except TimeoutError:
            raise
        except Exception:
            raise RuntimeError("capability_transport_unavailable") from None
