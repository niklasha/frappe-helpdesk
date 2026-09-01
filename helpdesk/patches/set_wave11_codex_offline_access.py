"""Make already-seeded Codex providers ask OpenAI to stay connected.

The openai_codex preset shipped with an empty scope, so the authorize request
carried no scope parameter and OpenAI minted no refresh token — every
connection made from it held an access token with a hard stop and nothing to
renew it. The preset now asks for what Codex CLI itself asks for; this patch
brings the providers seeded from the old preset along.

Only the preset's own emptiness is repaired. A scope an administrator wrote is
theirs, whatever it says — and an engine already connected under the old scope
still holds no refresh token: only a fresh grant can mint one, so those
connections need a re-connect from the settings page.
"""

import frappe

from helpdesk.api.ai_oauth import PRESETS


def execute():
    scope = PRESETS["openai_codex"]["scope"]
    for row in frappe.get_all(
        "HD AI OAuth Provider",
        filters={"preset": "openai_codex"},
        fields=["name", "scope"],
    ):
        if not (row.scope or "").strip():
            frappe.db.set_value("HD AI OAuth Provider", row.name, "scope", scope)
