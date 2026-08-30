"""Helpdesk's half of the raphain contract.

raphain is a Rust library crate: it has no service a Python process can call.
What it publishes is a configuration contract, so this module owns the part
Helpdesk can own honestly — describing the engines, and emitting the documents
a raphain-embedding runner consumes. No inference happens here.
"""

import frappe

from helpdesk.utils import agent_only

ENGINE_FIELDS = ["engine_name", "kind", "model", "base_url", "is_default"]


@frappe.whitelist()
@agent_only
def list_engines() -> list:
    """Return the AI engines Helpdesk is configured to describe, newest first."""
    return frappe.get_all(
        "HD AI Engine",
        filters={"enabled": 1},
        fields=ENGINE_FIELDS,
        order_by="creation desc",
    )
