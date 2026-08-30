import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from helpdesk.utils import agent_only


LANGUAGE_MARKERS = {
    "sv": ("och", "att", "för", "med", "beställning", "tack", "hej"),
    "en": ("the", "and", "please", "order", "thanks", "hello"),
    "de": ("und", "bitte", "bestellung", "danke", "hallo"),
    "da": ("og", "tak", "bestilling", "venligst", "hej"),
    "no": ("og", "takk", "bestilling", "vennligst", "hei"),
    "fi": ("ja", "kiitos", "tilaus", "hei"),
}


def detect_language_code(text):
    """Identify a message's language from the common words it uses."""
    words = set(re.findall(r"[a-zà-öø-ÿ]+", (text or "").lower()))
    scores = {
        code: len(words & set(markers)) for code, markers in LANGUAGE_MARKERS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else None


@frappe.whitelist()
@agent_only
def detect_language(text):
    """Return the detected language code of a message, or None when unclear."""
    return detect_language_code(text)


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
    source_language = source_language or detect_language_code(original_text)
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


@frappe.whitelist()
@agent_only
def get_customer_language(customer):
    """Return the language a customer prefers to be answered in."""
    return frappe.db.get_value("HD Customer", customer, "language")


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_inbound(
    ticket_id,
    original_text,
    translated_text=None,
    target_language="sv",
    provider=None,
    model_version=None,
    idempotency_key=None,
):
    """Record the Swedish translation of a message a customer sent us."""
    return record_translation(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=translated_text,
        target_language=target_language,
        direction="Inbound",
        provider=provider,
        model_version=model_version,
        idempotency_key=idempotency_key,
    )


def _ticket_customer_language(ticket_id):
    """Return the language stored on the customer this ticket belongs to."""
    customer = frappe.db.get_value("HD Ticket", ticket_id, "customer")
    return get_customer_language(customer) if customer else None


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_outbound(
    ticket_id,
    original_text,
    translated_text=None,
    target_language=None,
    provider=None,
    model_version=None,
    idempotency_key=None,
):
    """Record an agent's Swedish reply translated into the customer's language."""
    return record_translation(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=translated_text,
        source_language="sv",
        target_language=target_language or _ticket_customer_language(ticket_id),
        direction="Outbound",
        provider=provider,
        model_version=model_version,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def review_translation(translation_id, translated_text=None):
    """Record an agent's review, and any correction, of a translation."""
    doc = frappe.get_doc("HD Message Translation", translation_id)
    if translated_text:
        doc.translated_text = translated_text
    doc.reviewed = 1
    doc.reviewed_by = frappe.session.user
    doc.reviewed_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def mark_translation_sent(translation_id):
    """Send a translation; an outbound one must be reviewed by an agent first."""
    doc = frappe.get_doc("HD Message Translation", translation_id)
    if doc.direction == "Outbound" and not doc.reviewed:
        frappe.throw(_("This translation must be reviewed before it is sent."))
    doc.sent_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()
