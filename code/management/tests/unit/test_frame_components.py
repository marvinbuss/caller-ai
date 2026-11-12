"""Route table, sidebar order and role filtering of the authenticated frame."""

import pytest


def test_routes_match_the_shell_contract():
    from management_app.components.frame import LOGIN_ROUTE, REDIRECTS
    from management_app.security import PAGE_ROUTES

    assert LOGIN_ROUTE == "/login"
    assert PAGE_ROUTES == {
        "dashboard": "/",
        "communities": "/communities",
        "test": "/test",
        "status": "/status",
        "settings": "/settings",
    }
    assert REDIRECTS == {
        "/configuration": "/communities",
        "/configuration/communities/[community_id]": "/communities",
        "/configuration/test": "/test",
    }


def test_sidebar_order_and_labels():
    from management_app.components.frame import SIDEBAR

    assert [label for _page, label, _icon in SIDEBAR] == [
        "Dashboard",
        "Community and Agent",
        "Test",
        "Status",
        "Settings",
    ]


@pytest.mark.parametrize(
    "editor,tester,switch,expected",
    [
        (True, True, False, ["dashboard", "communities", "test", "status", "settings"]),
        (True, True, True, ["dashboard", "communities", "test", "status", "settings"]),
        (True, False, False, ["dashboard", "communities", "status", "settings"]),
        (True, False, True, ["dashboard", "communities", "status", "settings"]),
        (False, True, False, ["dashboard", "status"]),
        (False, True, True, ["dashboard", "test", "status"]),
        (False, False, False, []),
        (False, False, True, []),
    ],
)
def test_entries_follow_roles_and_the_tester_switch(editor, tester, switch, expected):
    from management_app.security import allowed_pages

    assert allowed_pages(editor=editor, tester=tester, tester_only_tests=switch) == expected


def test_sidebar_marks_only_the_current_entry():
    from management_app.components.frame import sidebar_entries

    entries = sidebar_entries("status")
    assert [entry[0] for entry in entries if entry[3]] == ["status"]
    assert [entry[1] for entry in entries] == ["/", "/communities", "/test", "/status", "/settings"]


@pytest.mark.parametrize(
    "value,accepted",
    [
        ("/", "/"),
        ("/status", "/status"),
        ("/communities", "/communities"),
        ("/login", None),
        ("//evil.example", None),
        ("https://evil.example/status", None),
        ("/status?x=1", None),
        ("/status#top", None),
        ("\\\\evil.example", None),
        ("/status/", None),
        ("", None),
        (None, None),
    ],
)
def test_return_path_accepts_only_exact_page_routes(value, accepted):
    from management_app.security import return_path

    assert return_path(value) == accepted


@pytest.mark.parametrize("page", ["dashboard", "communities", "test", "status", "settings"])
def test_every_page_renders_inside_the_frame(page):
    import reflex as rx

    from management_app.components.frame import frame

    component = frame(page, lambda: rx.text("content"))
    rendered = str(component.render())
    assert "CallerSessionGate" in rendered and "CallerSessionProbe" in rendered


def test_unknown_page_is_refused():
    import reflex as rx

    from management_app.components.frame import frame

    with pytest.raises(ValueError, match="unknown_page"):
        frame("admin", lambda: rx.text("content"))
