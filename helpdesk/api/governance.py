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


def _prompt_row(prompt_name: str, released: dict | None) -> dict:
    """Describe one prompt as an administrator needs to see it.

    Every prompt the code can ask for is described, released or not, because a
    library that lists only what someone has already edited hides exactly the
    six calls nobody has looked at yet.
    """
    from helpdesk.api import ai_generation

    built_in = ai_generation.PROMPTS.get(prompt_name, {})
    wording = (released or {}).get("prompt") or built_in.get("prompt", "")
    return {
        "prompt_name": prompt_name,
        "call": built_in.get("call"),
        "shared": bool(built_in.get("shared")),
        "purpose": (released or {}).get("purpose") or built_in.get("purpose", ""),
        "prompt": wording,
        "built_in": built_in.get("prompt", ""),
        # Whether anyone has tuned this call is the first thing to know about it,
        # and a released row whose wording matches the built-in one has not been.
        "customised": bool(released) and wording != built_in.get("prompt", ""),
        "version": (released or {}).get("version"),
        "enabled": cint((released or {}).get("enabled")),
        "released": bool(released),
    }


@frappe.whitelist()
@agent_only
def list_ai_prompts() -> list:
    """Return every prompt the AI calls read, with what governs each."""
    from helpdesk.api import ai_generation

    released = {
        row["prompt_name"]: row
        for row in frappe.get_all(
            "HD AI Prompt",
            fields=["prompt_name", "purpose", "prompt", "version", "enabled"],
        )
    }
    known = list(ai_generation.PROMPTS)
    # A prompt released under a name the code no longer asks for still governs
    # the call that falls back to it, so it is shown rather than hidden.
    known += [name for name in released if name not in ai_generation.PROMPTS]
    return [_prompt_row(name, released.get(name)) for name in known]


@frappe.whitelist()
@agent_only
def get_ai_prompt(prompt_name: str) -> dict:
    """Return one prompt as it now stands."""
    released = frappe.db.get_value(
        "HD AI Prompt",
        {"prompt_name": prompt_name},
        ["prompt_name", "purpose", "prompt", "version", "enabled"],
        as_dict=True,
    )
    return _prompt_row(prompt_name, released)


@frappe.whitelist(methods=["POST"])
@agent_only
def set_ai_prompt_enabled(prompt_name: str, enabled: int | bool) -> dict:
    """Turn one prompt on or off without losing what was written in it.

    Disabling is how an administrator steps back to the built-in wording while
    keeping a draft they are still working on.
    """
    require_ai_prompt_admin()
    if frappe.db.exists("HD AI Prompt", prompt_name):
        doc = frappe.get_doc("HD AI Prompt", prompt_name)
    else:
        from helpdesk.api import ai_generation

        doc = frappe.new_doc("HD AI Prompt")
        doc.prompt_name = prompt_name
        doc.prompt = ai_generation.built_in_prompt(prompt_name)
        doc.purpose = ai_generation.PROMPTS.get(prompt_name, {}).get("purpose")
    doc.enabled = cint(enabled)
    doc.save(ignore_permissions=True)
    log_configuration_change(
        "HD AI Prompt", doc.name, details=f"enabled={cint(enabled)}"
    )
    return get_ai_prompt(prompt_name)


@frappe.whitelist(methods=["POST"])
@agent_only
def reset_ai_prompt(prompt_name: str) -> dict:
    """Put one prompt back to the wording Helpdesk ships with.

    Tuning is only worth doing when it is reversible, and a reset is a change
    like any other: it takes a new version and is logged, so the wording that
    produced yesterday's generations stays reconstructable.
    """
    require_ai_prompt_admin()
    from helpdesk.api import ai_generation

    built_in = ai_generation.built_in_prompt(prompt_name)
    if not built_in:
        frappe.throw(
            _("{0} has no built-in wording to return to.").format(prompt_name)
        )
    upsert_ai_prompt(prompt_name=prompt_name, prompt=built_in)
    return get_ai_prompt(prompt_name)


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
