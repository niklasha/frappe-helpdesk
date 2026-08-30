import json

import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin

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

CONFIGURATION_ACTION = "changed configuration"

AUTOMATION_MANAGER_ROLE = "Helpdesk Automation Manager"
AI_MANAGER_ROLE = "Helpdesk AI Manager"
KNOWLEDGE_MANAGER_ROLE = "Helpdesk Knowledge Manager"
HELPDESK_ROLES = (AUTOMATION_MANAGER_ROLE, AI_MANAGER_ROLE, KNOWLEDGE_MANAGER_ROLE)


def _as_text(details):
    """Automation details reach the log as text, whatever shape the caller used."""
    if details is None or isinstance(details, str):
        return details
    return json.dumps(details)


@frappe.whitelist(methods=["POST"])
@agent_only
def log_automation_event(
    action: str,
    actor: str | None = "Automation",
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    details: dict | list | str | None = None,
) -> dict:
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
def log_ai_event(
    action: str,
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    details: dict | list | str | None = None,
) -> dict:
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
def automation_events(
    reference_doctype: str | None = None,
    reference_name: str | None = None,
    limit: int | None = 20,
) -> list:
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


@frappe.whitelist(methods=["POST"])
@agent_only
def log_configuration_change(
    reference_doctype: str,
    reference_name: str,
    details: dict | list | str | None = None,
) -> dict:
    """Record that a person changed one of the rules the helpdesk runs on."""
    return log_automation_event(
        CONFIGURATION_ACTION,
        actor="User",
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        details=details,
    )


@frappe.whitelist()
@agent_only
def configuration_changes(
    reference_doctype: str | None = None, limit: int | None = 100
) -> list:
    """Return the logged changes to critical configuration, newest first."""
    filters = {"actor": "User", "action": CONFIGURATION_ACTION}
    if reference_doctype:
        filters["reference_doctype"] = reference_doctype
    return frappe.get_all(
        "HD Automation Event",
        filters=filters,
        fields=EVENT_FIELDS,
        order_by="creation desc",
        limit_page_length=cint(limit) or 100,
    )


@frappe.whitelist()
@agent_only
def has_helpdesk_role(role: str) -> bool:
    """Whether the session user carries the given helpdesk role."""
    return role in frappe.get_roles(frappe.session.user)


def require_role(role):
    """Refuse the change unless the session user holds the role or administers the site."""
    if is_admin() or has_helpdesk_role(role):
        return
    frappe.throw(
        _("The role {0} is required to make this change.").format(role),
        frappe.PermissionError,
    )


def require_ai_prompt_admin():
    """Deciding what the AI is told to do is an administrative act."""
    require_role(AI_MANAGER_ROLE)


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_ai_prompt(
    prompt_name: str, prompt: str, purpose: str | None = None
) -> dict:
    """Create or update an AI prompt, recording the change as an event."""
    require_ai_prompt_admin()
    if frappe.db.exists("HD AI Prompt", prompt_name):
        doc = frappe.get_doc("HD AI Prompt", prompt_name)
    else:
        doc = frappe.new_doc("HD AI Prompt")
        doc.prompt_name = prompt_name
    doc.prompt = prompt
    if purpose is not None:
        doc.purpose = purpose
    doc.save(ignore_permissions=True)
    log_configuration_change("HD AI Prompt", doc.name, details=doc.prompt)
    return doc.as_dict()
