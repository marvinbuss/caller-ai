import json

import pytest

from management_app.services.formatted_text import NODES, to_tree


def node_types(tree):
    found = set()

    def walk(node):
        if isinstance(node, str):
            return
        found.add(node["type"])
        for child in node.get("children", []):
            walk(child)

    walk(tree)
    return found - {"root"}


def test_markdown_becomes_the_allowlisted_tree():
    tree = to_tree(
        "# Orders\n\n"
        "Your **open** orders and *notes*:\n\n"
        "- First item\n- Second `code`\n\n"
        "1. One\n2. Two\n\n"
        "> Quoted line  \nnext\n\n"
        "| Order | Status |\n| --- | --- |\n| 42 | Shipped |\n\n"
        "```\nprint('hi')\n```\n\n"
        "---\n\n"
        "[Docs](https://example.com/docs) ![Logo](https://example.com/logo.png)\n"
    )
    assert tree is not None and tree["type"] == "root"
    assert node_types(tree) <= NODES
    assert node_types(tree) >= {
        "h1",
        "p",
        "strong",
        "em",
        "ul",
        "ol",
        "li",
        "code",
        "blockquote",
        "br",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "pre",
        "hr",
        "a",
        "img",
    }
    encoded = json.dumps(tree)
    assert '"href": "https://example.com/docs"' in encoded
    assert '"src": "https://example.com/logo.png"' in encoded and '"alt": "Logo"' in encoded
    # Tight list items hold their text directly, without a paragraph.
    first_list = next(node for node in tree["children"] if node["type"] == "ul")
    assert first_list["children"][0] == {"type": "li", "children": ["First item"]}


def test_only_href_src_and_alt_attributes_are_produced():
    tree = to_tree("[a](https://example.com) ![b](https://example.com/b.png)")

    def walk(node):
        if isinstance(node, str):
            return
        assert set(node) <= {"type", "children", "href", "src", "alt"}
        for child in node.get("children", []):
            walk(child)

    walk(tree)


def test_raw_html_stays_literal_text():
    tree = to_tree('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">')
    encoded = json.dumps(tree)
    assert "<script>alert(1)</script>" in encoded
    assert node_types(tree) == {"p"}


def test_deeper_headings_are_shown_as_the_smallest_allowed_heading():
    tree = to_tree("#### Small heading")
    assert tree == {"type": "root", "children": [{"type": "h3", "children": ["Small heading"]}]}


def test_unsafe_link_schemes_are_not_links():
    tree = to_tree("[click](javascript:alert(1))")
    assert "a" not in node_types(tree)


@pytest.mark.parametrize(
    "markdown",
    [
        "- item\n" * 1100,
        "".join("> " * depth + "deep\n" for depth in range(1, 16)),
        "x" * (64 * 1024 + 1),
    ],
    ids=["nodes", "depth", "text"],
)
def test_over_the_limits_falls_back_to_plain_text(markdown):
    assert to_tree(markdown) is None


def test_non_text_input_falls_back():
    assert to_tree(None) is None  # type: ignore[arg-type]
