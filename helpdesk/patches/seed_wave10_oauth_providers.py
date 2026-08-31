"""Seed the OAuth providers an AI engine can be bound to.

The two vendor presets carry facts nothing can discover: OpenAI publishes no
dynamic client registration, so its public Codex client and that client's
loopback redirect are the only way in, and Anthropic's token endpoint takes JSON
behind a beta header. The third is the blank an administrator fills in by URL.
"""

import frappe

from helpdesk.api.ai_oauth import PRESETS


def execute():
    for provider_name, preset in PRESETS.items():
        if frappe.db.exists("HD AI OAuth Provider", provider_name):
            continue
        doc = frappe.new_doc("HD AI OAuth Provider")
        doc.provider_name = provider_name
        doc.update(preset)
        doc.insert(ignore_permissions=True)
