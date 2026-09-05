"""The shared half of every AI generator Helpdesk runs.

Wave 7 wired one path by hand: read the instructions an administrator owns, ask
the runner, keep the provenance. Every generator after it needs the same three
things, plus one more — a structured answer it can act on — so they live here
once instead of being written again per endpoint.

The runner contract is text out; in goes text, and since Wave 20 a picture
beside it when the ticket carries one. A structured result is therefore asked
for in the prompt and parsed back out of the text, and an answer that is not
the JSON the caller asked for is refused rather than half-understood.
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, strip_html

from helpdesk.api import ai_engine, ai_runner, governance
from helpdesk.utils import agent_only

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


@frappe.whitelist()
@agent_only
def engines_for(call: str | None = None) -> list:
    """The engines to ask for one call, in the order they should be asked.

    The routes pinned to the call come first, then the site's global order,
    then the single default engine the table replaces. Only enabled engines
    survive, and a site with no runner at all has no chain to walk.
    """
    if not ai_runner.is_runner_available():
        return []
    return ai_engine.engine_chain(call)


def engines_or_throw(call: str | None = None) -> list:
    """Return the chain to ask for one call, or refuse to generate at all.

    Generation without a runner has no honest fallback: an endpoint that
    quietly recorded a made-up result would be indistinguishable from one the
    model wrote, so the caller is told plainly that nothing is configured.
    """
    if not ai_runner.is_runner_available():
        frappe.throw(_("No AI runner is configured."))
    chain = ai_engine.engine_chain(call)
    if not chain:
        frappe.throw(_("No AI runner is configured: there is no default engine."))
    return chain


def engine_or_throw() -> str:
    """Return the single engine a caller that does not walk a chain should use."""
    return engines_or_throw()[0]


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


# The profile fields a customer row carries (Wave 19, CUST-01), in the order
# the model is shown them. Read as a list so a column the sibling slice has
# not yet added fails loudly here, not silently as an empty profile.
CUSTOMER_PROFILE_FIELDS = (
    "customer_name",
    "key_account",
    "default_product",
    "proof_required",
    "delivery_default",
    "desk_notes",
)


def customer_context(ticket_id: str) -> str:
    """The resolved customer's profile, as the data block a model reads.

    The order coordinator's rule about the key accounts — they order Stark
    "till 99 %" and never write it — used to live as five names in the prompt
    text, and went stale the day a sixth signed. The profile on the customer
    row is the rule as data: who is writing, that it is a key account, what it
    orders when it does not say. Empty when the ticket has no customer, so an
    unknown sender is shown to the model exactly as before.
    """
    customer = frappe.db.get_value("HD Ticket", ticket_id, "customer")
    if not customer:
        return ""
    meta = frappe.get_meta("HD Customer")
    fields = [f for f in CUSTOMER_PROFILE_FIELDS if meta.has_field(f)]
    if not fields:
        return ""
    profile = frappe.db.get_value("HD Customer", customer, fields, as_dict=True)
    if not profile:
        return ""
    for field in CUSTOMER_PROFILE_FIELDS:
        # A column the profile migration has not created yet reads as empty.
        profile.setdefault(field, None)
    yes_no = lambda value: "ja" if cint(value) else "nej"  # noqa: E731
    lines = [
        "Kundprofil:",
        f"Kund: {profile.customer_name or customer}",
        f"Nyckelkund (key account): {yes_no(profile.key_account)}",
    ]
    if profile.default_product:
        lines.append(
            f"Standardprodukt: {profile.default_product} "
            "(beställer denna när ingen produkt anges)"
        )
    lines.append(f"Korrektur krävs: {yes_no(profile.proof_required)}")
    if profile.delivery_default:
        lines.append(f"Standardleverans: {profile.delivery_default}")
    if profile.desk_notes:
        lines.append(f"Anteckningar: {profile.desk_notes}")
    return "\n".join(lines)


def with_customer_context(ticket_id: str, content: str) -> str:
    """`content` with the customer's profile appended, when there is one."""
    context = customer_context(ticket_id)
    return f"{content}\n\n{context}" if context else content


# The inventory of a ticket's files (Wave 20, FILE-01) — one row per File on
# the ticket or on one of its messages, classified from the bytes. Read here
# so the model is told what the bytes say before it is shown the picture.
TICKET_FILE = "HD Ticket File"
TICKET_FILE_FIELDS = (
    "file",
    "file_name",
    "file_url",
    "format",
    "kind",
    "vector",
    "relevance",
    "source",
    "width",
    "height",
    "pages",
)
# What is shown to the model as a picture: the raster formats a provider's
# image input accepts. A raster PDF is named in the text, not shown.
IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
MAX_IMAGE_PARTS = 4
MAX_IMAGE_BYTES = 1_500_000
ATTACHMENTS_HEADING = "Bifogade filer (bedömda från innehållet, inte från namnet):"


def _ticket_file_rows(ticket_id: str) -> list[dict]:
    """The inventory's rows for one ticket, or nothing when there is none.

    The doctype belongs to the sibling slice; a site migrated without it
    reads as a ticket without files rather than as an error in the triage.
    Only the columns the doctype actually has are asked for.
    """
    if not frappe.db.table_exists(TICKET_FILE):
        return []
    meta = frappe.get_meta(TICKET_FILE)
    link = next(
        (f.fieldname for f in meta.fields if f.fieldtype == "Link" and f.options == "HD Ticket"),
        None,
    )
    if not link:
        return []
    fields = ["name"] + [f for f in TICKET_FILE_FIELDS if meta.has_field(f)]
    return frappe.get_all(
        TICKET_FILE,
        filters={link: ticket_id},
        fields=fields,
        order_by="creation asc",
        limit_page_length=0,
    )


def _file_line(row: dict) -> str:
    """`logo.png: raster, png, 4×4` — the verdict the bytes already gave."""
    details = [str(row.get("kind") or "okänd").lower(), row.get("format") or "?"]
    if row.get("width") and row.get("height"):
        details.append(f"{cint(row['width'])}×{cint(row['height'])} px")
    elif row.get("pages"):
        details.append(f"{cint(row['pages'])} sidor")
    return f"- {row.get('file_name')}: " + ", ".join(details)


def _file_name(row: dict) -> str | None:
    """The name of the File record behind one inventory row, or None."""
    name = row.get("file")
    if not name and row.get("file_url"):
        name = frappe.db.get_value("File", {"file_url": row["file_url"]}, "name")
    return name or None


def _image_part(row: dict) -> dict | None:
    """One raster file as the data URL an image part carries, or None.

    None when the file is not a picture a provider takes, cannot be read, or
    is bigger than the cap: a photo from a phone is not worth the tokens,
    and the text line already says what it is.
    """
    mime = IMAGE_MIME.get(str(row.get("format") or "").lower())
    if not mime or str(row.get("kind") or "") != "Raster":
        return None
    name = _file_name(row)
    if not name:
        return None
    # The size is a column; the bytes are a read from disk or a bucket. A
    # photo from a phone is refused on the column, before anything is opened.
    if cint(frappe.db.get_value("File", name, "file_size")) > MAX_IMAGE_BYTES:
        return None
    try:
        content = frappe.get_doc("File", name).get_content()
    except Exception:
        return None
    if isinstance(content, str):
        content = content.encode()
    if not content or len(content) > MAX_IMAGE_BYTES:
        return None
    return {
        "type": "image",
        "data_url": f"data:{mime};base64,{base64.b64encode(content).decode()}",
    }


def _sync_inventory(ticket_id: str) -> None:
    """Have the sibling's inventory read files it has not reached yet.

    The inventory's own job may still be queued when the triage runs; a
    triage that read the rows as they stood would miss the mail's
    attachments. The module belongs to the other slice, so its absence is a
    site without an inventory, not an error in the triage; and a failure to
    read a file is the inventory's to log, never the triage's to fail on.
    """
    try:
        from helpdesk.api import ticket_files
    except ImportError:
        return
    sync = getattr(ticket_files, "_sync", None)
    if not sync or not frappe.db.table_exists(TICKET_FILE):
        return
    try:
        sync(ticket_id, reclassify=False)
    except Exception:
        frappe.log_error(
            title="Helpdesk AI triage",
            message=f"could not inventory the files on {ticket_id}\n\n{frappe.get_traceback()}",
        )


def attachments_context(ticket_id: str) -> tuple[str, list[dict]]:
    """(the inventory as text, the raster images as parts) for one ticket.

    The rehearsal's "PNG, inte vektoriserad" was the customer's own words
    read back (K7, B9). Here the model is told what the bytes say — one line
    per file, the deterministic verdict — and shown the picture beside it,
    so what it says about a file is about the file. At most MAX_IMAGE_PARTS
    pictures travel; the rest are still named in the text. Empty when the
    ticket has no files.
    """
    _sync_inventory(ticket_id)
    rows = _ticket_file_rows(ticket_id)
    if not rows:
        return "", []
    lines = [ATTACHMENTS_HEADING] + [_file_line(row) for row in rows]
    parts = []
    for row in rows:
        if len(parts) >= MAX_IMAGE_PARTS:
            break
        part = _image_part(row)
        if part:
            parts.append(part)
    if parts:
        lines.append(
            f"{len(parts)} av filerna visas som bild nedan. Bedöm varje fil i "
            "attachment_assessment och nämn den vid filnamn; motsäg aldrig "
            "verdiktet ovan."
        )
    return "\n".join(lines), parts


def with_attachments(content: str, attachments_text: str) -> str:
    """`content` with the file inventory appended, when there is one."""
    return f"{content}\n\n{attachments_text}" if attachments_text else content


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def record_file_assessment(ticket_id: str, assessment: str | None) -> list[str]:
    """Put what the model said about a file on that file's inventory row.

    The triage's `attachment_assessment` is a paragraph; the strip needs the
    sentence about logo.png on logo.png's row, marked as the model's, so the
    desk can tell the bytes' verdict from the model's opinion. Only a row the
    text names by file name is written; a paragraph that names no file is
    left where it is. Returns the rows written.
    """
    text = (assessment or "").strip()
    if not text:
        return []
    meta = frappe.get_meta(TICKET_FILE) if frappe.db.table_exists(TICKET_FILE) else None
    if not meta or not meta.has_field("assessment") or not meta.has_field("assessed_by"):
        return []
    written = []
    sentences = _sentences(text)
    for row in _ticket_file_rows(ticket_id):
        file_name = str(row.get("file_name") or "")
        if not file_name or file_name.casefold() not in text.casefold():
            continue
        about = [s for s in sentences if file_name.casefold() in s.casefold()] or [text]
        frappe.db.set_value(
            TICKET_FILE,
            row["name"],
            {"assessment": " ".join(about), "assessed_by": "model"},
            update_modified=False,
        )
        written.append(row["name"])
    return written


def _messages(
    instructions: str, schema_hint: str, content: str, image_parts: list[dict] | None = None
) -> list:
    """Show the engine its instructions and the shape of the answer, then the material.

    The user turn is a plain string unless pictures travel with it; then it
    is a list of parts, text first (Wave 20). The string form is kept for
    every text-only call, so the wire the earlier waves assert on is unchanged.
    """
    user = (
        [{"type": "text", "text": content}, *image_parts] if image_parts else content
    )
    return [
        {"role": "system", "content": f"{instructions}\n\n{schema_hint}"},
        {"role": "user", "content": user},
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


# What one generation used and what it cost, in the field names every AI
# record keeps beside its provenance (Wave 17b). The counts are the runner's
# `usage`; the cost is computed here from the engine's own price list, so a
# price entered later can be re-run over the tokens that were kept.
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
COST_FIELDS = ("engine", *TOKEN_FIELDS, "ai_cost", "cost_known")


def cost_of(usage: dict | None, pricing: dict | None) -> float | None:
    """Return what one generation cost in USD, or None when nothing can be said.

    The arithmetic is raphain 0.3.2's `Pricing::cost_of` (session.rs): each
    count times its price per million tokens. A cache read without a price
    of its own is charged at a tenth of the input rate and a cache write at
    the input rate, as raphain does. Reasoning tokens are already inside the
    output count and are not added again.

    No usage or no price list means the cost is unknown, which is what None
    says here. The caller records that as `cost_known = 0` beside whatever
    number the column ends up holding: Frappe cannot keep a Currency column
    empty, so the flag, not the amount, is what tells "free" from "unpriced".
    """
    if not isinstance(usage, dict) or not usage:
        return None
    if not isinstance(pricing, dict) or not pricing:
        return None
    input_rate = flt(pricing.get("input_per_million"))
    output_rate = flt(pricing.get("output_per_million"))
    cache_write_rate = pricing.get("cache_write_per_million")
    cache_read_rate = pricing.get("cache_read_per_million")
    cache_write_rate = flt(cache_write_rate) if cache_write_rate is not None else input_rate
    cache_read_rate = flt(cache_read_rate) if cache_read_rate is not None else input_rate * 0.1
    return (
        flt(usage.get("input_tokens")) * input_rate
        + flt(usage.get("output_tokens")) * output_rate
        + flt(usage.get("cache_write_tokens")) * cache_write_rate
        + flt(usage.get("cache_read_tokens")) * cache_read_rate
    ) / 1e6


def _pricing(engine: str | None) -> dict | None:
    """Return the engine's price list, or None when it has none."""
    if not engine:
        return None
    stored = frappe.db.get_value("HD AI Engine", engine, "pricing")
    pricing = ai_engine._stored_document(stored)
    return pricing if isinstance(pricing, dict) else None


def usage_fields(response: dict, engine: str | None = None) -> dict:
    """Return the tokens one answer used, the engine that charged, and the cost.

    `cost_known` is 1 only when the runner reported usage and the engine has
    a price list; then `ai_cost` is the amount. Otherwise the cost is 0 with
    `cost_known` 0, which the ticket's sum leaves out: a runner from before
    accounting or an engine nobody has priced is not free, only unknown. A
    count the runner did not send is 0 as well, since an Int column cannot
    hold "nobody counted".

    The engine the response names is the engine that answered it, and it wins
    over the one the caller asked for: with a chain the two differ exactly when
    a fall-through happened, and the record must name the engine whose
    allowance was actually spent.
    """
    engine = response.get("engine") or engine
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = None
    counts = {
        field: (cint(usage.get(field)) if usage else 0) for field in TOKEN_FIELDS
    }
    cost = cost_of(usage, _pricing(engine))
    return {
        "engine": engine,
        **counts,
        "ai_cost": flt(cost),
        "cost_known": 1 if cost is not None else 0,
    }


def provenance(
    response: dict, prompt_version: int | str | None = None, engine: str | None = None
) -> dict:
    """Return what produced one result, in the field names the records use.

    An answer whose origin the runner did not state cannot be recorded: a
    result nobody can trace back to a model is indistinguishable from one a
    colleague wrote, which is the one thing the audit trail exists to tell
    apart. The refusal comes before the record, so nothing is left behind.

    With the engine named, the answer's token counts, their cost and whether
    that cost is known travel with the provenance (Wave 17b); a caller that
    names no engine records the tokens with `cost_known` 0.
    """
    if not response.get("model"):
        frappe.throw(_("The AI engine did not identify itself."))
    return {
        "provider": response.get("provider"),
        "model_version": response.get("model"),
        "prompt_version": prompt_version,
        **usage_fields(response, engine),
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


FAILOVER_ACTION = "ai_engine_failover"


def _named(engines: "list | str") -> list:
    """Return a chain as a list of engine names, however the caller spelled it."""
    if isinstance(engines, str):
        engines = [engines]
    return [name for name in (engines or []) if name]


def _answered(engines: "list | str", ask, call: str | None = None) -> dict:
    """Ask each engine in turn until one answers, and return its response.

    Only a transport failure walks on. A rate limit, an upstream failure and a
    runner that could not be reached all left the question unanswered, so the
    next engine — a second user on the same subscription, with its own
    allowance — may still answer it. Every other failure ends the chain where
    it happened: a refusal means the request was wrong and will be just as
    wrong on the next engine, and an answer that was rejected means the model
    already spoke. Asking again would only spend the second allowance.

    The response carries the name of the engine that produced it, so the
    provenance a caller records cannot name an engine that never answered.
    """
    names = _named(engines)
    if not names:
        frappe.throw(_("No AI runner is configured: there is no default engine."))
    failures: list = []
    unreachable = None
    for name in names:
        try:
            response = ask(name)
        except ai_runner.RunnerUnavailable as exception:
            failures.append((name, str(exception)))
            unreachable = exception
            continue
        response["engine"] = name
        for failed, reason in failures:
            governance.log_automation_event(
                action=FAILOVER_ACTION,
                details={
                    "call": call,
                    "failed_engine": failed,
                    "reason": reason,
                    "answered_by": name,
                },
            )
        return response
    # Every engine was out of reach. The last failure is raised as it was, so
    # a site with one engine reads in the log exactly as it did before.
    raise unreachable


def generate_text(
    engines: "list | str",
    instructions: str,
    content: str,
    task_hint: str,
    call: str | None = None,
) -> tuple[str, dict]:
    """Ask the chain for one piece of finished prose, with its provenance.

    `engines` is the order to try, or a single engine name; the first one that
    answers wins.

    An empty answer is a failed generation rather than an empty result: a blank
    message recorded as the model's own would reach a customer looking like
    something somebody meant to write. It is the model having answered badly,
    not an engine being unreachable, so the chain stops there.
    """
    response = _answered(
        engines,
        lambda engine: ai_runner.generate(
            engine=engine, messages=_messages(instructions, task_hint, content)
        ),
        call,
    )
    text = (response.get("text") or "").strip()
    if not text:
        frappe.throw(_("The AI engine returned no text."))
    return text, response


def generate_json(
    engines: "list | str",
    instructions: str,
    content: str,
    schema_hint: str,
    required_keys: tuple[str, ...] = (),
    image_parts: list[dict] | None = None,
    call: str | None = None,
) -> tuple[dict, dict]:
    """Ask the chain for one JSON object, and return it with its provenance.

    `engines` is the order to try, or a single engine name; an engine that is
    out of reach falls through to the next, an answer that is not the object
    asked for does not.

    Nothing is salvaged from an answer that is not that object: an engine that
    replied with prose has not answered the question that was asked, and a
    half-parsed result recorded as the model's own is worse than no result.

    An object that states none of the keys the caller asked about is refused
    for the same reason. It parses, but it answers nothing, and recording it
    would leave a hollow row that looks like a result somebody could act on.

    `image_parts` are the pictures shown beside the material (Wave 20); with
    any, the user turn goes out as a content list rather than a string.
    """
    # The demand for JSON is the call's, not the prompt's. A site's tuned
    # fragment for a call (the order desk's extraction wording, seeded from the
    # coordinator's document) can end in prose about logo files and never say
    # "answer with JSON"; the model then answers in prose and the whole
    # extraction is refused below. So the sentence is appended here whenever
    # the composed instructions do not already carry it.
    if JSON_ONLY.split(".")[0] not in instructions and JSON_ONLY.split(".")[0] not in schema_hint:
        schema_hint = f"{schema_hint}\n\n{JSON_ONLY}".strip()
    def ask(engine: str) -> dict:
        try:
            return ai_runner.generate(
                engine=engine,
                messages=_messages(instructions, schema_hint, content, image_parts),
            )
        except ai_runner.RunnerRejected:
            if not image_parts:
                raise
            # A runner from before Wave 20 does not take a content list. The
            # pictures are dropped and the text goes alone, so a stale runner
            # costs the model its look at the files rather than the whole triage.
            frappe.log_error(
                title="Helpdesk AI runner",
                message=(
                    "the runner rejected a message with image parts; "
                    "retrying with text only\n\n" + frappe.get_traceback()
                ),
            )
            return ai_runner.generate(
                engine=engine, messages=_messages(instructions, schema_hint, content)
            )

    response = _answered(engines, ask, call)
    try:
        answer = json.loads(_unfenced(response.get("text")))
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        frappe.throw(_("The AI engine did not return the expected JSON."))
    if required_keys and not any(_stated(answer.get(key)) for key in required_keys):
        frappe.throw(_("The AI engine did not return the expected JSON."))
    return answer, response
