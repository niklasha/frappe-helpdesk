"""Helpdesk's half of the raphain contract.

raphain is a Rust library crate: it has no service a Python process can call.
What it publishes is a configuration contract, so this module owns the part
Helpdesk can own honestly — describing the engines, and emitting the documents
a raphain-embedding runner consumes. No inference happens here.
"""

import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin

ENGINE_FIELDS = ["engine_name", "kind", "model", "base_url", "is_default"]


def _require_admin() -> None:
    """Choosing which model the helpdesk talks to is an administrative act."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator may configure AI engines."),
            frappe.PermissionError,
        )


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


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_engine(
    engine_name: str,
    kind: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Create or update one AI engine, and return it as configured."""
    _require_admin()
    if frappe.db.exists("HD AI Engine", engine_name):
        doc = frappe.get_doc("HD AI Engine", engine_name)
    else:
        doc = frappe.new_doc("HD AI Engine")
        doc.engine_name = engine_name
    if kind is not None:
        doc.kind = kind
    if model is not None:
        doc.model = model
    if base_url is not None:
        doc.base_url = base_url
    if enabled is not None:
        doc.enabled = cint(enabled)
    doc.save(ignore_permissions=True)
    return doc.as_dict()
