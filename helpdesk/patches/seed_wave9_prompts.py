"""Seed the prompts the remaining AI generators are instructed with.

The built-in wording in `helpdesk.api.ai_generation` is only a fallback. Seeding
it into the library gives an administrator something to read, edit and version
before the first ticket is generated against it.
"""

import frappe

from helpdesk.api.ai_generation import PROMPTS


def execute():
    for prompt_name, prompt in PROMPTS.items():
        if frappe.db.exists("HD AI Prompt", prompt_name):
            continue
        frappe.get_doc(
            {
                "doctype": "HD AI Prompt",
                "prompt_name": prompt_name,
                "purpose": prompt["purpose"],
                "prompt": prompt["prompt"],
                "version": 1,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
