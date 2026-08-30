"""Seed the prompt the AI answers knowledge questions with."""

import frappe

from helpdesk.api.ai_reply import (
    KNOWLEDGE_REPLY_INSTRUCTIONS,
    KNOWLEDGE_REPLY_PROMPT_NAME,
)


def execute():
    if frappe.db.exists("HD AI Prompt", KNOWLEDGE_REPLY_PROMPT_NAME):
        return
    frappe.get_doc(
        {
            "doctype": "HD AI Prompt",
            "prompt_name": KNOWLEDGE_REPLY_PROMPT_NAME,
            "purpose": "Answer a customer question from the approved knowledge library",
            "prompt": KNOWLEDGE_REPLY_INSTRUCTIONS,
            "enabled": 1,
        }
    ).insert(ignore_permissions=True)
