import frappe
from frappe import _

from helpdesk.utils import agent_only


def _validate_supported_language(language_code):
    """Refuse correspondence in a language the company has not enabled."""
    if not language_code:
        return
    if not frappe.db.exists(
        "HD Supported Language", {"language_code": language_code, "enabled": 1}
    ):
        frappe.throw(_("Language {0} is not enabled for translation.").format(language_code))


@frappe.whitelist(methods=["POST"])
@agent_only
def record_translation(
    ticket_id,
    original_text,
    translated_text=None,
    source_language=None,
    target_language=None,
    direction="Inbound",
    provider=None,
    model_version=None,
    idempotency_key=None,
):
    """Persist a translation next to its original text, replayable by key."""
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    _validate_supported_language(source_language)
    _validate_supported_language(target_language)
    if idempotency_key:
        existing = frappe.db.get_value(
            "HD Message Translation", {"idempotency_key": idempotency_key}, "name"
        )
        if existing:
            return frappe.get_doc("HD Message Translation", existing).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD Message Translation",
            "ticket": ticket_id,
            "direction": direction,
            "original_text": original_text,
            "translated_text": translated_text,
            "source_language": source_language,
            "target_language": target_language,
            "provider": provider,
            "model_version": model_version,
            "idempotency_key": idempotency_key,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def list_supported_languages():
    """Return the languages currently enabled for customer correspondence."""
    return frappe.get_all(
        "HD Supported Language",
        filters={"enabled": 1},
        fields=["language_code", "language_name"],
        order_by="language_code asc",
    )
