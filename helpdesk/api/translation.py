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


def normalise_language(code):
    """Reduce a language tag to the language: «sv-SE», «sv_SE» and «SV» are all «sv».

    A customer card says what the customer reads as a locale as often as as a
    language, and the working language is a bare code. Compared raw, «sv-SE»
    is not «sv» and a Swedish ticket is held for translation into Swedish.
    """
    if not code:
        return None
    return re.split(r"[-_]", str(code).strip().lower(), maxsplit=1)[0] or None


def same_language(a, b):
    """Whether two language tags name the same language, whatever their locale."""
    return normalise_language(a) is not None and normalise_language(a) == normalise_language(b)


def _marker_scores(text):
    words = set(re.findall(r"[a-zà-öø-ÿ]+", (text or "").lower()))
    return {
        code: len(words & set(markers)) for code, markers in LANGUAGE_MARKERS.items()
    }


def detect_language_code(text):
    """Identify a message's language from the common words it uses."""
    scores = _marker_scores(text)
    best = max(scores, key=scores.get)
    return best if scores[best] else None


def is_confidently_in(text, language):
    """Whether the marker list can say, without a rival, that `text` is in `language`.

    The detector is six words per language and a tie goes to whichever
    language was listed first, which is no basis for stopping a reply. This
    holds only when `language`'s markers are present and no other language
    scores as high; when the detector cannot tell, the answer is no.
    """
    code = normalise_language(language)
    if not code or code not in LANGUAGE_MARKERS:
        return False
    scores = _marker_scores(text)
    own = scores[code]
    return own > 0 and all(score < own for other, score in scores.items() if other != code)


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
    sent_side: str = "Translation",
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

    `sent_side` records which of the row's two texts actually left the house.
    Nearly always the translation: an agent writes the working language and the
    customer receives our rendering of it. The exception is a reply the agent
    wrote in the customer's language themselves, where the original is what was
    sent and the translation beside it is the archive's copy — a row the thread
    must not display, and `reply_translated` must not send, as though the
    customer had read the translation.
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
            "sent_side": sent_side,
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
    engines = ai_generation.engines_or_throw(ai_generation.TRANSLATION_INBOUND)
    instructions, prompt_version = ai_generation._prompt(
        ai_generation.TRANSLATION_INBOUND
    )
    answer, response = ai_generation.generate_json(
        engines,
        instructions,
        original_text,
        _inbound_hint(target_language),
        required_keys=("language",),
        call=ai_generation.TRANSLATION_INBOUND,
    )
    return answer, ai_generation.provenance(response, prompt_version)


def _generated_translation(original_text, source_language, target_language, prompt_name):
    """Return the engine's translation of one message, with its provenance.

    A failed generation is left to surface. Recording the original text in the
    translation's place would read, to an agent, exactly like a message that
    needed no translation.
    """
    engines = ai_generation.engines_or_throw(prompt_name)
    instructions, prompt_version = ai_generation._prompt(prompt_name)
    text, response = ai_generation.generate_text(
        engines,
        instructions,
        original_text,
        _translation_hint(source_language, target_language),
        call=prompt_name,
    )
    return text, ai_generation.provenance(response, prompt_version)


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
    # Which of the two texts the customer actually received (Wave 25b).
    "sent_side",
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


@frappe.whitelist()
@agent_only
def reply_language(ticket_id: str) -> dict:
    """Return the language this ticket should be answered in, and where that came from.

    Three sources, read in order of how much they are worth: the customer card
    is a person's own statement of what they read, the thread is what the
    ingress detected from the words they actually wrote, and the working
    language is what is left when neither says anything. The source travels
    with the answer so the editor can say «norska, enligt kundkortet» instead
    of asserting a language nobody can trace back.

    The reading lives here rather than in each caller because the editor, the
    reply endpoint and whatever comes next would otherwise each carry their own
    copy, and three copies of a rule disagree.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    working = get_working_language()

    language = _ticket_customer_language(ticket_id)
    source = "customer"
    if not language:
        detected = frappe.get_all(
            "HD Message Translation",
            filters={"ticket": ticket_id, "direction": "Inbound"},
            fields=["source_language"],
            order_by="creation desc",
            limit_page_length=1,
        )
        language = detected[0]["source_language"] if detected else None
        source = "thread"
    if not language:
        language, source = working, "working"

    return {
        "language": language,
        "source": source,
        "working_language": working,
        "needs_translation": 0 if same_language(language, working) else 1,
    }


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_outbound(ticket_id: str, text: str, hold: int | bool = 0) -> dict:
    """Translate an agent's reply into the customer's language, without sending it.

    A draft is a draft: the row is recorded unreviewed and unsent, because the
    customer must not learn what the desk is about to say before an agent has
    read it. Sending is a separate door, and pressing it is the review.

    A ticket already in the working language buys nothing at all — the engine
    is not asked, so translating Swedish into Swedish costs neither a call nor
    a row. Noticing afterwards that the answer was the same would be paid for
    all the same.

    `hold` is the Send button asking, not the language button: it translates
    only text that is plainly in the working language, and answers «no
    translation needed» for text the agent wrote in the customer's language
    or text the detector cannot place. The button translates what it is given.
    """
    reply = reply_language(ticket_id)
    if not reply["needs_translation"]:
        return {
            "needs_translation": 0,
            "language": reply["language"],
            "source": reply["source"],
            "text": text,
        }
    if frappe.utils.cint(hold) and not is_confidently_in(text, reply["working_language"]):
        return {
            "needs_translation": 0,
            "reason": "not in the working language",
            "language": reply["language"],
            "source": reply["source"],
            "text": text,
        }

    row = generate_outbound_translation(
        ticket_id=ticket_id, original_text=text, target_language=reply["language"]
    )
    return {
        "needs_translation": 1,
        "language": reply["language"],
        "source": reply["source"],
        "translation": row["name"],
        "name": row["name"],
        "original_text": row.get("original_text") or text,
        "translated_text": row.get("translated_text"),
        "reviewed": row.get("reviewed") or 0,
        "sent_on": row.get("sent_on"),
    }


def hold_untranslated_reply(ticket_id: str, message: str) -> dict | None:
    """Draft the translation of a reply that must not go out as written.

    LANG-08: `reply_translated` has been the careful door since Wave 22, and
    the ordinary Send button is the careless one beside it — it puts whatever
    the editor holds on the wire. On a ticket answered in another language,
    text in the working language is text the customer cannot read, sent by a
    desk that promises to answer in theirs.

    So the reply is held rather than dropped: the working-language text is
    drafted into the customer's language and the unsent, unreviewed row is
    returned, which is exactly what the agent should have had in the first
    place. The caller refuses the send and points at that row.

    Nothing is held when the ticket is answered in the working language, nor
    when the text is already in the customer's language — the agent who used
    the language button, or wrote the customer's language themselves, is not
    stopped, and nobody pays to translate a language into itself.
    """
    reply = reply_language(ticket_id)
    if not reply["needs_translation"]:
        return None

    plain = frappe.utils.strip_html_tags(message or "").strip()
    if not plain:
        return None
    # Only the house's own language is held, and only when the detector says
    # so without a rival. An unrecognised or ambiguous text is left alone: a
    # false hold costs a model call and confuses the agent, while a missed one
    # is caught by the archive copy, which is the cheaper mistake.
    if not is_confidently_in(plain, reply["working_language"]):
        return None

    # A second attempt with the same words finds the first attempt's draft.
    # Every hold that generated afresh would leave the previous row behind,
    # unreviewed and unsent, for nobody.
    waiting = frappe.get_all(
        "HD Message Translation",
        filters={
            "ticket": ticket_id,
            "direction": "Outbound",
            "original_text": plain,
            "sent_on": ("is", "not set"),
        },
        fields=["name"],
        order_by="creation desc",
        limit_page_length=1,
    )
    if waiting:
        return frappe.get_doc("HD Message Translation", waiting[0]["name"]).as_dict()

    return generate_outbound_translation(
        ticket_id=ticket_id,
        original_text=plain,
        target_language=reply["language"],
    )
