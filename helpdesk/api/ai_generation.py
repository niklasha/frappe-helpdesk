"""The shared half of every AI generator Helpdesk runs.

Wave 7 wired one path by hand: read the instructions an administrator owns, ask
the runner, keep the provenance. Every generator after it needs the same three
things, plus one more — a structured answer it can act on — so they live here
once instead of being written again per endpoint.

The runner contract stays text in, text out. A structured result is therefore
asked for in the prompt and parsed back out of the text, and an answer that is
not the JSON the caller asked for is refused rather than half-understood.
"""

import json

import frappe
from frappe import _
from frappe.utils import strip_html

from helpdesk.api import ai_engine, ai_runner, governance

FENCE = "```"

JSON_ONLY = (
    "Answer with one JSON object and nothing else. Leave a field out entirely "
    "when the message does not say what it should be; never invent a value."
)

# The order coordinator's rule for a customer reply on an open quote or proof
# (Wave 17, B12). The demo filed "godkänner offerten, men gör västarna gröna" as
# a quote change because nobody had told the model an approval with changes is
# still an approval. Shared by the built-in wording and the site seed, so the
# two say the same thing.
TRIAGE_APPROVAL_RULE = (
    "A customer reply that approves a proof or quote while asking for changes "
    "is still Korrektur godkänt: an approval with changes is an approval, and "
    "the desk makes the changes before the order. A change of price, quantity "
    "or delivery without a proof in play is Offertändring. A reply that only "
    "supplies details the desk asked for, such as a size or a quantity, keeps "
    "the earlier classification of the thread."
)

TEXT_ONLY = (
    "Answer with the finished text and nothing else: no preamble, no "
    "explanation, and no detail the material you were given does not support."
)

# One prompt per call. Sharing a prompt between two calls looked economical and
# was not: tuning the common-question answer also retuned every knowledge reply,
# and there was no way to make outbound translation more formal than inbound
# without moving both.
KNOWLEDGE_REPLY = "knowledge_reply"
COMMON_QUESTION = "common_question"
COMPLETION_REQUEST = "completion_request"
TICKET_TRIAGE = "ticket_triage"
ORDER_EXTRACTION = "order_extraction"
TRANSLATION_INBOUND = "translation_inbound"
TRANSLATION_OUTBOUND = "translation_outbound"

# Prepended to every call's instructions when an administrator enables it: the
# place to say once what would otherwise be copied into seven prompts and drift.
# It names no call of its own.
SHARED_PREAMBLE = "shared_preamble"

# The names two of these calls answered to before Wave 11 split them. A site
# that tuned the shared wording keeps reading it until it tunes the specific
# call it meant, so splitting a prompt never silently drops a site's tuning.
MESSAGE_TRANSLATION = "message_translation"
LEGACY_PROMPT_NAMES = {
    COMMON_QUESTION: KNOWLEDGE_REPLY,
    TRANSLATION_INBOUND: MESSAGE_TRANSLATION,
    TRANSLATION_OUTBOUND: MESSAGE_TRANSLATION,
}

KNOWLEDGE_REPLY_INSTRUCTIONS = (
    "Answer the customer question using only the approved knowledge below. "
    "When the knowledge does not cover the question, say so plainly instead of "
    "guessing, and offer to pass the question to a colleague."
)

TRANSLATION_INSTRUCTIONS = (
    "You translate helpdesk correspondence for a Swedish print shop. "
    "Keep the meaning, the tone and every order number, size and date "
    "exactly as they stand, and translate nothing that is already in "
    "the target language."
)

PROMPTS = {
    KNOWLEDGE_REPLY: {
        "call": "draft_knowledge_reply",
        "purpose": "Answer a customer question from the approved knowledge library",
        "prompt": KNOWLEDGE_REPLY_INSTRUCTIONS,
    },
    COMMON_QUESTION: {
        "call": "answer_common_question",
        "purpose": "Answer a recurring question such as delivery times or product facts",
        "prompt": (
            "You answer the questions this helpdesk is asked over and over — "
            "delivery times, formats, what a product is made of. Answer from "
            "the approved knowledge below and nothing else, in one or two "
            "sentences a customer can act on. When the knowledge does not "
            "cover it, say so and offer to pass the question to a colleague."
        ),
    },
    TICKET_TRIAGE: {
        "call": "triage_ticket",
        "purpose": "Classify an incoming ticket and propose how to handle it",
        "prompt": (
            "You triage incoming messages for a Swedish print shop's helpdesk. "
            "Read the ticket and judge what it is about, how urgent it is, and "
            "what a colleague would need to know before picking it up. Base "
            "every field on what the ticket actually says.\n\n"
            + TRIAGE_APPROVAL_RULE
            + "\n\n"
            + JSON_ONLY
        ),
    },
    ORDER_EXTRACTION: {
        "call": "extract_order",
        "purpose": "Read the order details a customer's message states",
        "prompt": (
            "You read order enquiries for a Swedish print shop. Report only the "
            "details the message states, in the customer's own terms. Do not "
            "decide whether the order is complete and do not fill a gap with a "
            "likely value: a missing field is the useful answer, because the "
            "helpdesk asks the customer about it.\n\n" + JSON_ONLY
        ),
    },
    TRANSLATION_INBOUND: {
        "call": "generate_inbound_translation",
        "purpose": (
            "Detect a customer message's language, and translate it into the "
            "language agents work in when it is not already that"
        ),
        # Detection rides in the same call as the translation, because a word
        # list proved too weak a detector (Wave 14): the model states the
        # language it read, and only then translates — or declines, when the
        # message is already in the working language.
        "prompt": (
            "You are the language desk of a helpdesk. First identify the "
            "language the customer's message is written in. Then, unless it is "
            "already the requested target language, translate it. "
            + TRANSLATION_INSTRUCTIONS
            + " This translation is read by a colleague deciding what to do, so "
            "stay literal where literal and fluent disagree. Answer with a JSON "
            'object holding "language" (the ISO 639-1 code of the language the '
            'message arrived in) and "translation" (the translated text, or '
            "null when the message is already in the target language).\n\n"
            + JSON_ONLY
        ),
    },
    TRANSLATION_OUTBOUND: {
        "call": "generate_outbound_translation",
        "purpose": "Translate a reply into the language the customer writes in",
        "prompt": (
            TRANSLATION_INSTRUCTIONS
            + " This translation is what the customer reads, so it must sound "
            "like it was written in their language rather than converted into "
            "it.\n\n" + TEXT_ONLY
        ),
    },
    COMPLETION_REQUEST: {
        "call": "generate_completion_request",
        "purpose": "Ask a customer for the order details still missing",
        "prompt": (
            "You write short, friendly Swedish replies asking a customer for "
            "the order details the helpdesk still needs. Ask only for the "
            "fields you are given, name them in plain language, and promise "
            "nothing about price or delivery.\n\n" + TEXT_ONLY
        ),
    },
    SHARED_PREAMBLE: {
        "call": None,
        "shared": True,
        # Seeded switched off, and that is the whole compatibility story: with
        # it on, a generation is produced from two prompts and records a version
        # naming both. A site that never turns it on keeps recording the bare
        # version every earlier wave asserts against.
        "enabled": False,
        "purpose": "House style prepended to every AI call, when enabled",
        "prompt": (
            "Svara på svenska om inget annat efterfrågas. Hitta aldrig på en "
            "uppgift: säg rakt ut när underlaget inte räcker. Lova aldrig pris "
            "eller leveranstid som inte står i underlaget."
        ),
    },
}


def built_in_prompt(name: str) -> str:
    """Return the wording a call falls back to when nothing is released."""
    return PROMPTS.get(name, {}).get("prompt", "")


def _fragment(name: str) -> tuple[str | None, int | None]:
    """Return one released fragment's wording and version, or nothing.

    Only an enabled row counts. Disabling a prompt is how an administrator
    steps back to the built-in wording without losing what they wrote.
    """
    row = frappe.db.get_value(
        "HD AI Prompt",
        {"prompt_name": name, "enabled": 1},
        ["prompt", "version"],
        as_dict=True,
    )
    if row and row.prompt:
        return row.prompt, row.version
    return None, None


def _version_label(version) -> str:
    """Name a fragment's version, including when it has none.

    Built-in wording carries no version, because nothing released it — and that
    is a fact an auditor needs stated rather than left blank.
    """
    return str(version) if version else "builtin"


def _prompt(name: str) -> tuple[str, int | str | None]:
    """Return one call's instructions and the version they came from.

    An administrator owns what the AI is told, so a released prompt wins over
    the built-in wording, and the name this call answered to before Wave 11 wins
    over the built-in wording too.

    With the shared fragment enabled the instructions are composed from two
    prompts, and one version number can no longer describe them. The recorded
    version then names both. A site that leaves the fragment alone keeps
    recording exactly the bare version it always did.
    """
    source, instructions, version = name, None, None
    for candidate in (name, LEGACY_PROMPT_NAMES.get(name)):
        if not candidate:
            continue
        instructions, version = _fragment(candidate)
        if instructions:
            source = candidate
            break
    if not instructions:
        instructions, version = built_in_prompt(name), None

    preamble, preamble_version = _fragment(SHARED_PREAMBLE)
    if not preamble:
        return instructions, version
    return (
        f"{preamble}\n\n{instructions}",
        f"{SHARED_PREAMBLE}:{_version_label(preamble_version)}"
        f"+{source}:{_version_label(version)}",
    )


def engine_or_throw() -> str:
    """Return the engine to generate with, or refuse to generate at all.

    Generation without a runner has no honest fallback: an endpoint that
    quietly recorded a made-up result would be indistinguishable from one the
    model wrote, so the caller is told plainly that nothing is configured.
    """
    if not ai_runner.is_runner_available():
        frappe.throw(_("No AI runner is configured."))
    engine = ai_engine.default_engine()
    if not engine:
        frappe.throw(_("No AI runner is configured: there is no default engine."))
    return engine


def ticket_text(ticket_id: str) -> str:
    """Return what a ticket says, as the prose an engine should be shown.

    The description is stored as HTML for the desk to render; the markup is
    noise to a model, so only the words the customer wrote are passed on.
    """
    ticket = frappe.db.get_value(
        "HD Ticket", ticket_id, ["subject", "description"], as_dict=True
    )
    if not ticket:
        frappe.throw(_("Ticket {0} was not found.").format(ticket_id))
    parts = [ticket.subject, strip_html(ticket.description or "")]
    return "\n\n".join(part for part in parts if part)


def _messages(instructions: str, schema_hint: str, content: str) -> list:
    """Show the engine its instructions and the shape of the answer, then the material."""
    return [
        {"role": "system", "content": f"{instructions}\n\n{schema_hint}"},
        {"role": "user", "content": content},
    ]


def _unfenced(text: str | None) -> str:
    """Return the JSON a model wrapped in a markdown code fence, or the text as-is."""
    body = (text or "").strip()
    if not body.startswith(FENCE):
        return body
    body = body[len(FENCE) :]
    if body[:4].lower() == "json":
        body = body[4:]
    end = body.rfind(FENCE)
    return (body[:end] if end != -1 else body).strip()


def provenance(response: dict, prompt_version: int | str | None = None) -> dict:
    """Return what produced one result, in the field names the records use.

    An answer whose origin the runner did not state cannot be recorded: a
    result nobody can trace back to a model is indistinguishable from one a
    colleague wrote, which is the one thing the audit trail exists to tell
    apart. The refusal comes before the record, so nothing is left behind.
    """
    if not response.get("model"):
        frappe.throw(_("The AI engine did not identify itself."))
    return {
        "provider": response.get("provider"),
        "model_version": response.get("model"),
        "prompt_version": prompt_version,
    }


def replayed(doctype: str, idempotency_key: str | None) -> str | None:
    """Return the record one idempotency key already produced, if any.

    A generator asks this before it asks the engine. A replay is a repeat of a
    question already answered, so it must cost neither a generation nor a
    second audit entry: the stored record is the answer, unchanged.
    """
    if not idempotency_key:
        return None
    return frappe.db.get_value(doctype, {"idempotency_key": idempotency_key}, "name")


def _stated(value: object) -> bool:
    """Return whether one field of an answer actually says something."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def attribute(
    action: str, reference_doctype: str, reference_name: str, generation: dict
) -> dict:
    """Log that the AI, not the agent who asked, produced one record.

    The agent's session ran the generation, so without this the audit log would
    show a person authoring text they only requested. The provenance travels
    with the event, which is what makes a result traceable back to the prompt
    and model version that wrote it.
    """
    return governance.log_ai_event(
        action,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        details=generation,
    )


def generate_text(
    engine: str, instructions: str, content: str, task_hint: str
) -> tuple[str, dict]:
    """Ask the engine for one piece of finished prose, with its provenance.

    An empty answer is a failed generation rather than an empty result: a blank
    message recorded as the model's own would reach a customer looking like
    something somebody meant to write.
    """
    response = ai_runner.generate(
        engine=engine, messages=_messages(instructions, task_hint, content)
    )
    text = (response.get("text") or "").strip()
    if not text:
        frappe.throw(_("The AI engine returned no text."))
    return text, response


def generate_json(
    engine: str,
    instructions: str,
    content: str,
    schema_hint: str,
    required_keys: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    """Ask the engine for one JSON object, and return it with its provenance.

    Nothing is salvaged from an answer that is not that object: an engine that
    replied with prose has not answered the question that was asked, and a
    half-parsed result recorded as the model's own is worse than no result.

    An object that states none of the keys the caller asked about is refused
    for the same reason. It parses, but it answers nothing, and recording it
    would leave a hollow row that looks like a result somebody could act on.
    """
    response = ai_runner.generate(
        engine=engine, messages=_messages(instructions, schema_hint, content)
    )
    try:
        answer = json.loads(_unfenced(response.get("text")))
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        frappe.throw(_("The AI engine did not return the expected JSON."))
    if required_keys and not any(_stated(answer.get(key)) for key in required_keys):
        frappe.throw(_("The AI engine did not return the expected JSON."))
    return answer, response
