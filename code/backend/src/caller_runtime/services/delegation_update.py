"""What the voice assistant may delegate: delegation updates and the static block (feature 007, R5).

The voice assistant only decides whether to delegate; the agents choose who handles a request.
So it is told what the backend can do, never who: one line per reachable community, its routing
description (its name only when the description is empty), cut to 160 characters. The reachable
set is the active agent's own community, the communities connected from it, and the routable
communities reachable through Routing; before a community is active it is every routable one.

With `CALLER_VOICE_DELEGATION_UPDATES=true` an update (at most 2,000 bytes, in parts of at most
500 bytes at line boundaries) goes out at the start and whenever the reachable set changes. With
the default `false`, `static_delegation_block` is appended to the start instructions instead. When
not every community fits, the list ends with "- …and more capabilities" and the voice assistant is
told to delegate other requests too, so no request is refused because its community was cut.
Everything here comes from the published configuration; no user text is ever included.
"""

from typing import Any, Protocol

from caller_runtime.domain.configuration import RevisionSnapshot

LINE_CHARS = 160
UPDATE_BYTES = 2000
BLOCK_BYTES = 4096
PART_BYTES = 500
UPDATE_HEADER = "Delegation update. This replaces all earlier delegation updates."
BLOCK_HEADER = "Delegation scope for this call:"
TOOLS = "Backend tools:"
FITS = "Generate <|SpawnThinking|> when the request needs one of these capabilities."
NO_FIT = (
    "If no listed capability fits, say briefly that this service cannot help with it and "
    "offer what it can do."
)
CUT = "For other requests, Generate <|SpawnThinking|> too; the backend decides whether it can help."
MORE = "- …and more capabilities"


class Position(Protocol):
    @property
    def active_agent_id(self) -> str: ...

    @property
    def active_community_id(self) -> str | None: ...


def _usable(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Enabled communities whose entry agent is enabled, by id."""
    agents = {agent["agentId"]: agent for agent in graph["agents"]}
    return {
        community["communityId"]: community
        for community in graph["communities"]
        if community["enabled"] is True
        and agents.get(community["entryAgentId"], {}).get("enabled") is True
    }


def _ordered(communities: list[dict[str, Any]]) -> tuple[str, ...]:
    ordered = sorted(communities, key=lambda item: (item["name"].casefold(), item["communityId"]))
    return tuple(item["communityId"] for item in ordered)


def reachable(snapshot: RevisionSnapshot, slot: Position) -> tuple[str, ...]:
    """The communities whose capabilities the voice assistant may delegate now, by name."""
    graph = snapshot.for_runtime()
    usable = _usable(graph)
    routable = [item for item in usable.values() if item.get("routable", True) is True]
    own = slot.active_community_id
    if own is None or own not in usable:
        return _ordered(routable)
    connected = {
        edge["targetCommunityId"]
        for edge in graph["communityConnections"]
        if edge["sourceCommunityId"] == own and edge["targetCommunityId"] in usable
    }
    chosen = {own, *connected, *(item["communityId"] for item in routable)}
    return _ordered([usable[identifier] for identifier in chosen])


def changed(
    snapshot: RevisionSnapshot, slot: Position, previous: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """The new reachable set if an update is due, otherwise `None` (R5)."""
    current = reachable(snapshot, slot)
    return None if current == previous else current


def _lines(snapshot: RevisionSnapshot, communities: tuple[str, ...]) -> list[str]:
    usable = _usable(snapshot.for_runtime())
    lines = []
    for identifier in communities:
        community = usable[identifier]
        text = " ".join(str(community["routingDescription"]).split()) or community["name"]
        lines.append("- " + text[:LINE_CHARS])
    return lines


def _compose(header: str, tools: list[str], limit: int) -> str:
    def size(lines: list[str]) -> int:
        return len("\n".join(lines).encode("utf-8"))

    if size([header, TOOLS, *tools, FITS, NO_FIT]) <= limit:
        return "\n".join([header, TOOLS, *tools, FITS, NO_FIT])
    kept: list[str] = []
    for line in tools:
        if size([header, TOOLS, *kept, line, MORE, FITS, CUT]) > limit:
            break
        kept.append(line)
    return "\n".join([header, TOOLS, *kept, MORE, FITS, CUT])


def _parts(text: str) -> list[str]:
    """Split at line boundaries into parts of at most 500 bytes; each keeps its newline."""
    parts, current = [], ""
    for line in text.split("\n"):
        piece = line if not current else "\n" + line
        if current and len((current + piece).encode("utf-8")) + 1 > PART_BYTES:
            parts.append(current + "\n")
            current = line
        else:
            current += piece
    parts.append(current)
    return parts


def delegation_update(
    snapshot: RevisionSnapshot,
    slot: Position,
    communities: tuple[str, ...] | None = None,
) -> list[str]:
    """The delegation update for this position, in parts for `session.instructions.append`."""
    chosen = reachable(snapshot, slot) if communities is None else communities
    return _parts(_compose(UPDATE_HEADER, _lines(snapshot, chosen), UPDATE_BYTES))


def static_delegation_block(snapshot: RevisionSnapshot, *, limit: int = BLOCK_BYTES) -> str:
    """The block appended to the start instructions when delegation updates are off."""
    graph = snapshot.for_runtime()
    routable = [item for item in _usable(graph).values() if item.get("routable", True) is True]
    return _compose(BLOCK_HEADER, _lines(snapshot, _ordered(routable)), min(limit, BLOCK_BYTES))
