"""Community and Agent page at `/communities`, inside the authenticated frame (editor role)."""

import reflex as rx

from management_app.components.frame import frame


def community_agent_page() -> rx.Component:
    from management_app.pages.communities import configuration_body

    return frame("communities", configuration_body)
