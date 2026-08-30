import json

import frappe
from frappe.utils import cint

from helpdesk.utils import agent_only

EVENT_FIELDS = [
    "name",
    "actor",
    "action",
    "reference_doctype",
    "reference_name",
    "details",
    "occurred_on",
    "performed_by",
]


def _as_text(details):
    """Automation details reach the log as text, whatever shape the caller used."""
    if details is None or isinstance(details, str):
        return details
    return json.dumps(details)


@frappe.whitelist(methods=["POST"])
@agent_only
def log_automation_event(
    action, actor="Automation", reference_doctype=None, reference_name=None, details=None
):
    """Record one action taken by automation so that it can be audited later."""
    doc = frappe.get_doc(
        {
            "doctype": "HD Automation Event",
            "actor": actor,
            "action": action,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "details": _as_text(details),
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def log_ai_event(action, reference_doctype=None, reference_name=None, details=None):
    """Record an action an AI took, attributed to the AI rather than the user."""
    return log_automation_event(
        action,
        actor="AI",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        details=details,
    )


@frappe.whitelist()
@agent_only
def automation_events(reference_doctype=None, reference_name=None, limit=20):
    """Return recorded automation events, newest first."""
    filters = {}
    if reference_doctype:
        filters["reference_doctype"] = reference_doctype
    if reference_name:
        filters["reference_name"] = reference_name
    return frappe.get_all(
        "HD Automation Event",
        filters=filters,
        fields=EVENT_FIELDS,
        order_by="creation desc",
        limit_page_length=cint(limit) or 20,
    )
