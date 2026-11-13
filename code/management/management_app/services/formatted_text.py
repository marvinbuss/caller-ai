"""Turns a completed text reply written in Markdown into an allowlisted node tree for the browser.

The reply is parsed with `markdown-it-py` (CommonMark plus tables) with raw HTML and linkify off,
so HTML in a reply stays literal text. Only the nodes in `NODES` are produced, with text as plain
strings and `href`, `src` and `alt` as the only attributes. A reply that needs any other node, or
that exceeds the node, depth or text limits, gives `None`, and the browser keeps the plain-text row.
The browser still checks every node and keeps a URL only when it is `https:`.
"""

from typing import Any, Final

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

MAX_NODES: Final = 2000
MAX_DEPTH: Final = 12
MAX_TEXT_BYTES: Final = 64 * 1024
NODES: Final = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "strong",
        "em",
        "br",
        "hr",
        "a",
        "img",
    }
)
_CONTAINERS: Final = {
    "paragraph": "p",
    "bullet_list": "ul",
    "ordered_list": "ol",
    "list_item": "li",
    "blockquote": "blockquote",
    "table": "table",
    "thead": "thead",
    "tbody": "tbody",
    "tr": "tr",
    "th": "th",
    "td": "td",
    "strong": "strong",
    "em": "em",
}
_PARSER: Final = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")

Node = dict[str, Any] | str


class _Unsupported(Exception):
    pass


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.text = 0

    def node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _Unsupported

    def string(self, value: str) -> str:
        self.node()
        self.text += len(value.encode("utf-8"))
        if self.text > MAX_TEXT_BYTES:
            raise _Unsupported
        return value


def _plain(node: SyntaxTreeNode) -> str:
    if node.type in {"text", "code_inline"}:
        return node.content
    if node.type in {"softbreak", "hardbreak"}:
        return " "
    return "".join(_plain(child) for child in node.children)


def _children(node: SyntaxTreeNode, depth: int, budget: _Budget) -> list[Node]:
    result: list[Node] = []
    for child in node.children:
        result.extend(_convert(child, depth, budget))
    return result


def _convert(node: SyntaxTreeNode, depth: int, budget: _Budget) -> list[Node]:
    if depth > MAX_DEPTH:
        raise _Unsupported
    kind = node.type
    if kind == "inline" or (kind == "paragraph" and node.hidden):
        # Inline wrappers and the hidden paragraphs of tight lists add no element.
        return _children(node, depth, budget)
    if kind == "text":
        return [budget.string(node.content)]
    if kind == "softbreak":
        return [budget.string("\n")]
    if kind in {"hardbreak", "hr"}:
        budget.node()
        return [{"type": "br" if kind == "hardbreak" else "hr"}]
    budget.node()
    if kind == "heading":
        level = min(int(node.tag[1:]), 3)
        return [{"type": f"h{level}", "children": _children(node, depth + 1, budget)}]
    if kind in {"fence", "code_block"}:
        return [{"type": "pre", "children": [budget.string(node.content)]}]
    if kind == "code_inline":
        return [{"type": "code", "children": [budget.string(node.content)]}]
    if kind == "link":
        return [
            {
                "type": "a",
                "href": str(node.attrs.get("href", "")),
                "children": [budget.string(_plain(node))],
            }
        ]
    if kind == "image":
        alt = _plain(node)
        budget.string(alt)
        return [{"type": "img", "src": str(node.attrs.get("src", "")), "alt": alt}]
    if kind in _CONTAINERS:
        return [{"type": _CONTAINERS[kind], "children": _children(node, depth + 1, budget)}]
    raise _Unsupported


def to_tree(markdown: str) -> dict[str, Any] | None:
    """Return `{"type": "root", "children": [...]}`, or `None` to keep the plain-text row."""
    if not isinstance(markdown, str) or len(markdown.encode("utf-8")) > MAX_TEXT_BYTES:
        return None
    try:
        root = SyntaxTreeNode(_PARSER.parse(markdown))
        return {"type": "root", "children": _children(root, 1, _Budget())}
    except (_Unsupported, RecursionError, ValueError):
        return None
