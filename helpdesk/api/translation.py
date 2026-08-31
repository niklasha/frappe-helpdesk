import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from helpdesk.api import ai_generation
from helpdesk.utils import agent_only


DEFAULT_WORKING_LANGUAGE = "sv"

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
def detect_language(text: str) -> str | None:
    """Return the detected language code of a message, or None when unclear."""
    return detect_language_code(text)


@frappe.whitelist()
@agent_only
def get_working_language() -> str:
    """Return the language agents read tickets and write replies in.

    The Kravspec says Swedish, and Swedish is what a site ships with. It is
    still a setting: a helpdesk that opens a second desk, or is sold on, changes
    one value rather than four literals across two modules — and a reply
    recorded as written in a language the agent does not work in mislabels the
    original LANG-03 requires stays available.
    """
    return (
        frappe.db.get_single_value("HD Settings", "working_language")
        or DEFAULT_WORKING_LANGUAGE
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def set_working_language(language_code: str) -> str:
    """Move the whole helpdesk to another working language."""
    from helpdesk.api.ai_runner import is_admin

    if not is_admin():
        frappe.throw(
            _("Only an administrator may change the working language."),
            frappe.PermissionError,
        )
    # An unsupported working language does not degrade translation, it refuses
    # every message in both directions.
    if not frappe.db.exists(
        "HD Supported Language", {"language_code": language_code, "enabled": 1}
    ):
        frappe.throw(
            _("{0} is not an enabled language, so the helpdesk cannot work in it.").format(
                language_code
            )
        )
    frappe.db.set_single_value("HD Settings", "working_language", language_code)
    return language_code


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
    ticket_id: str,
    original_text: str,
    translated_text: str | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    direction: str = "Inbound",
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
) -> dict:
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
            "prompt_version": prompt_version,
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
def get_customer_language(customer: str) -> str | None:
    """Return the language a customer prefers to be answered in."""
    return frappe.db.get_value("HD Customer", customer, "language")


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_inbound(
    ticket_id: str,
    original_text: str,
    translated_text: str | None = None,
    target_language: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Record the translation of a message a customer sent us.

    An unstated target is the language this helpdesk works in, which is what an
    inbound translation is for: putting the message in front of an agent.
    """
    return record_translation(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=translated_text,
        target_language=target_language or get_working_language(),
        direction="Inbound",
        provider=provider,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
    )


def _translation_hint(source_language, target_language):
    """Tell the engine which way to translate, naming the source when it is known.

    A guessed source language is not worth passing on, so an undetected one is
    simply left out and the engine reads it from the message itself.
    """
    hint = f"Translate the message into the language with the code {target_language}."
    if source_language:
        hint += f" It is written in the language with the code {source_language}."
    return hint


def _generated_translation(original_text, source_language, target_language, prompt_name):
    """Return the engine's translation of one message, with its provenance.

    A failed generation is left to surface. Recording the original text in the
    translation's place would read, to an agent, exactly like a message that
    needed no translation.
    """
    engine = ai_generation.engine_or_throw()
    instructions, prompt_version = ai_generation._prompt(prompt_name)
    text, response = ai_generation.generate_text(
        engine,
        instructions,
        original_text,
        _translation_hint(source_language, target_language),
    )
    return text, ai_generation.provenance(response, prompt_version)


@frappe.whitelist(methods=["POST"])
@agent_only
def generate_inbound_translation(
    ticket_id: str,
    original_text: str,
    target_language: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Translate a message a customer sent us, and record it beside the original.

    What the customer actually wrote is stored unchanged: the translation is a
    reading aid for the agent, never a replacement for the words that arrived.

    The languages are checked before the engine is asked. A language the
    company has not enabled ends the same way whenever it is caught, so it is
    caught while it is still free, and a key already used returns its record.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    stored = ai_generation.replayed("HD Message Translation", idempotency_key)
    if stored:
        return frappe.get_doc("HD Message Translation", stored).as_dict()
    source_language = detect_language_code(original_text)
    target_language = target_language or get_working_language()
    _validate_supported_language(source_language)
    _validate_supported_language(target_language)
    text, generation = _generated_translation(
        original_text, source_language, target_language, ai_generation.TRANSLATION_INBOUND
    )
    result = translate_inbound(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=text,
        target_language=target_language,
        provider=generation["provider"],
        model_version=generation["model_version"],
        prompt_version=generation["prompt_version"],
        idempotency_key=idempotency_key,
    )
    ai_generation.attribute(
        "translated an incoming message",
        "HD Message Translation",
        result["name"],
        generation,
    )
    return result


def _ticket_customer_language(ticket_id):
    """Return the language stored on the customer this ticket belongs to."""
    customer = frappe.db.get_value("HD Ticket", ticket_id, "customer")
    return get_customer_language(customer) if customer else None


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_outbound(
    ticket_id: str,
    original_text: str,
    translated_text: str | None = None,
    target_language: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Record an agent's Swedish reply translated into the customer's language."""
    return record_translation(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=translated_text,
        source_language=get_working_language(),
        target_language=target_language or _ticket_customer_language(ticket_id),
        direction="Outbound",
        provider=provider,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def generate_outbound_translation(
    ticket_id: str,
    original_text: str,
    target_language: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Translate an agent's Swedish reply into the language the customer reads.

    Which language that is has to be known rather than guessed: answering in
    the wrong one is a worse failure than not translating at all, so an unknown
    customer language stops the generation instead of picking a likely one.

    The result is recorded as any other outbound translation, which means an
    agent still reviews it before the customer sees it.

    A language the company has not enabled is refused before the engine is
    asked, and a key that already produced a translation returns it unchanged.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    stored = ai_generation.replayed("HD Message Translation", idempotency_key)
    if stored:
        return frappe.get_doc("HD Message Translation", stored).as_dict()
    target_language = target_language or _ticket_customer_language(ticket_id)
    if not target_language:
        frappe.throw(
            _("The language this customer reads is not known, so this reply cannot be translated.")
        )
    _validate_supported_language(target_language)
    text, generation = _generated_translation(
        original_text,
        get_working_language(),
        target_language,
        ai_generation.TRANSLATION_OUTBOUND,
    )
    result = translate_outbound(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=text,
        target_language=target_language,
        provider=generation["provider"],
        model_version=generation["model_version"],
        prompt_version=generation["prompt_version"],
        idempotency_key=idempotency_key,
    )
    ai_generation.attribute(
        "translated a reply", "HD Message Translation", result["name"], generation
    )
    return result


@frappe.whitelist(methods=["POST"])
@agent_only
def review_translation(
    translation_id: str, translated_text: str | None = None
) -> dict:
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
def mark_translation_sent(translation_id: str) -> dict:
    """Send a translation; an outbound one must be reviewed by an agent first."""
    doc = frappe.get_doc("HD Message Translation", translation_id)
    if doc.direction == "Outbound" and not doc.reviewed:
        frappe.throw(_("This translation must be reviewed before it is sent."))
    doc.sent_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()
