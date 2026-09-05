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


def _validate_message(message, ticket_id):
    """Refuse a message that belongs to a different ticket, or to none.

    An unchecked link is how one customer's words end up displayed inside
    another customer's case. The view trusts this field to decide which
    paragraph it is allowed to replace, so the check belongs here, at the point
    the link is written, rather than in each place that reads it.
    """
    if not message:
        return
    owner = frappe.db.get_value(
        "Communication",
        message,
        ["reference_doctype", "reference_name"],
        as_dict=True,
    )
    if not owner:
        frappe.throw(_("Message {0} was not found.").format(message))
    if owner.reference_doctype != "HD Ticket" or owner.reference_name != ticket_id:
        frappe.throw(
            _("Message {0} does not belong to ticket {1}.").format(message, ticket_id)
        )


def _validate_adopted_from(adopted_from, ticket_id):
    """Refuse to wear a translation that belongs to another ticket, or to none."""
    if not adopted_from:
        return
    owner = frappe.db.get_value("HD Message Translation", adopted_from, "ticket")
    if not owner:
        frappe.throw(_("Translation {0} was not found.").format(adopted_from))
    if owner != ticket_id:
        frappe.throw(
            _("Translation {0} does not belong to ticket {1}.").format(
                adopted_from, ticket_id
            )
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def record_translation(
    ticket_id: str,
    original_text: str,
    translated_text: str | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
    direction: str = "Inbound",
    message: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
    adopted_from: str | None = None,
    engine: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    ai_cost: float | None = None,
    cost_known: int | bool | None = 0,
) -> dict:
    """Persist a translation next to its original text, replayable by key.

    The engine, the token counts and the cost (Wave 17b) say what this row
    bought. An adopted row names none of them: it wears another row's words
    and paid for nothing, so its cost stays empty and adds nothing to the
    ticket's sum.

    `message` names the Communication these words arrived in. Without it a
    translation can only be shown beside the ticket rather than beside the
    paragraph it translates, which is the whole reason the agent view could
    manage nothing better than a strip. It stays optional: every translation
    recorded before Wave 13 has no message to name, and a guess would say
    something false about what a customer wrote.

    `adopted_from` names the row whose translation these words wear. When an
    email opens a ticket its text is translated once, for the ticket, and the
    opening message shows the same words through a row of its own that says so
    — a row that bought nothing, and whose provenance is the row it names. It
    keeps the two facts apart that one row used to carry: which translation
    was paid for, and which message reads through it.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    _validate_message(message, ticket_id)
    _validate_adopted_from(adopted_from, ticket_id)
    source_language = source_language or detect_language_code(original_text)
    _validate_supported_language(source_language)
    _validate_supported_language(target_language)
    if idempotency_key:
        existing = frappe.db.get_value(
            "HD Message Translation", {"idempotency_key": idempotency_key}, "name"
        )
        if existing:
            return frappe.get_doc("HD Message Translation", existing).as_dict()
    costs = {
        "engine": engine,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "ai_cost": ai_cost,
        "cost_known": cost_known,
    }
    doc = frappe.get_doc(
        {
            "doctype": "HD Message Translation",
            "ticket": ticket_id,
            "message": message,
            "direction": direction,
            "original_text": original_text,
            "translated_text": translated_text,
            "source_language": source_language,
            "target_language": target_language,
            "provider": provider,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "idempotency_key": idempotency_key,
            "adopted_from": adopted_from,
            **costs,
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
    source_language: str | None = None,
    target_language: str | None = None,
    message: str | None = None,
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
    engine: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    ai_cost: float | None = None,
    cost_known: int | bool | None = 0,
) -> dict:
    """Record the translation of a message a customer sent us.

    An unstated target is the language this helpdesk works in, which is what an
    inbound translation is for: putting the message in front of an agent.
    """
    return record_translation(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=translated_text,
        source_language=source_language,
        target_language=target_language or get_working_language(),
        direction="Inbound",
        message=message,
        provider=provider,
        model_version=model_version,
        prompt_version=prompt_version,
        idempotency_key=idempotency_key,
        engine=engine,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        ai_cost=ai_cost,
        cost_known=cost_known,
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


def _inbound_hint(target_language):
    """Ask for detection and translation in one answer.

    The engine names the language before anything else, so the ordinary case —
    a message already in the working language — costs the call but no wasted
    translation, and the recorded source is the model's own claim rather than
    a word-list guess.
    """
    return (
        "First identify the language of the message and state it as an ISO "
        '639-1 code in "language". If that language is already '
        f'"{target_language}", set "translation" to null. Otherwise translate '
        f'the message into the language with the code "{target_language}" and '
        'put the result in "translation".'
    )


def _detected_translation(original_text, target_language):
    """Return the engine's combined verdict on one message, with provenance."""
    engine = ai_generation.engine_or_throw()
    instructions, prompt_version = ai_generation._prompt(
        ai_generation.TRANSLATION_INBOUND
    )
    answer, response = ai_generation.generate_json(
        engine,
        instructions,
        original_text,
        _inbound_hint(target_language),
        required_keys=("language",),
    )
    return answer, ai_generation.provenance(response, prompt_version, engine)


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
    return text, ai_generation.provenance(response, prompt_version, engine)


@frappe.whitelist(methods=["POST"])
@agent_only
def generate_inbound_translation(
    ticket_id: str,
    original_text: str,
    target_language: str | None = None,
    message: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Detect a customer message's language and record its translation.

    What the customer actually wrote is stored unchanged: the translation is a
    reading aid for the agent, never a replacement for the words that arrived.

    Returns the recorded row, or ``{"recorded": False, "reason": ...}`` when
    the engine's answer means there is nothing to record — the message is
    already in the working language, or in one the catalogue has not enabled.
    A key already used returns its record without a second call.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    stored = ai_generation.replayed("HD Message Translation", idempotency_key)
    if stored:
        return frappe.get_doc("HD Message Translation", stored).as_dict()
    target_language = target_language or get_working_language()
    _validate_supported_language(target_language)

    # The model detects; the word list no longer gates. Six languages with a
    # handful of marker words each filed most short or formal mail as
    # undetectable, and an undetected English mail sat in front of a Swedish
    # agent looking exactly like a mail that needed nothing. Detection rides in
    # the same call as the translation — one call per message, and a detector
    # that cannot disagree with its own translator.
    answer, generation = _detected_translation(original_text, target_language)
    source_language = str(answer.get("language") or "").strip().lower()
    if not source_language:
        frappe.throw(_("The AI engine did not name the message's language."))

    # One call was spent either way; these two outcomes just record nothing.
    # Already the working language is the ordinary case and the cheap answer.
    # A language the catalogue refuses is not correspondence we translate,
    # whoever detected it — recording it would put an unreviewable language
    # into the thread.
    if source_language == target_language:
        return {"recorded": False, "reason": "already in the working language",
                "source_language": source_language}
    if not frappe.db.exists(
        "HD Supported Language", {"language_code": source_language, "enabled": 1}
    ):
        return {"recorded": False, "reason": "language not enabled",
                "source_language": source_language}

    text = str(answer.get("translation") or "").strip()
    if not text:
        frappe.throw(_("The AI engine named a language but returned no translation."))

    result = translate_inbound(
        ticket_id=ticket_id,
        original_text=original_text,
        translated_text=text,
        source_language=source_language,
        target_language=target_language,
        message=message,
        **generation,
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
    engine: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    ai_cost: float | None = None,
    cost_known: int | bool | None = 0,
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
        engine=engine,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        ai_cost=ai_cost,
        cost_known=cost_known,
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
        **generation,
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


TRANSLATION_VIEW_FIELDS = (
    "name",
    # Which message these words arrived in. A view that cannot read this can
    # only put the translation beside the ticket, never beside the paragraph.
    "message",
    "direction",
    "source_language",
    "target_language",
    "original_text",
    "translated_text",
    "reviewed",
    "reviewed_by",
    "sent_on",
    "provider",
    "model_version",
    "prompt_version",
    # What the call used and what it charged (Wave 17b). An adopted row
    # shows nothing here: it bought nothing.
    "engine",
    "input_tokens",
    "output_tokens",
    "ai_cost",
    "cost_known",
    # Whose translation this row wears, when it bought none of its own. The
    # band uses it to stay silent about words the thread already shows.
    "adopted_from",
)


@frappe.whitelist()
@agent_only
def ticket_translations(ticket_id: str) -> list:
    """Return every translation recorded for one ticket, oldest first.

    LANG-03 says the original must always be available, and available means
    reachable: both halves have been kept since Wave 4 and no endpoint ever
    handed them to anything an agent looks at. Oldest first because the
    conversation reads that way.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    return frappe.get_all(
        "HD Message Translation",
        filters={"ticket": ticket_id},
        fields=list(TRANSLATION_VIEW_FIELDS),
        order_by="creation asc",
    )
