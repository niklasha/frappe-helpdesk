"""Tell the seeded providers which dialect their backend speaks.

The Codex backend answers the Responses API; OpenAI has retired chat
completions. Wave 10 seeded the providers before the engine had any way to know
that, so an engine bound to openai_codex could be saved on kind "openai" and
would then post to a path that no longer exists.
"""

import frappe

from helpdesk.api.ai_oauth import PRESETS


def execute():
    for provider_name, preset in PRESETS.items():
        kind = preset.get("engine_kind")
        if not kind or not frappe.db.exists("HD AI OAuth Provider", provider_name):
            continue
        if frappe.db.get_value("HD AI OAuth Provider", provider_name, "engine_kind"):
            continue
        frappe.db.set_value("HD AI OAuth Provider", provider_name, "engine_kind", kind)
