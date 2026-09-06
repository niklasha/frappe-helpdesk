import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, strip_html

from helpdesk.api import ai_generation, ai_runner, translation
from helpdesk.api.knowledge_library import search_knowledge
from helpdesk.utils import agent_only

KNOWLEDGE_REPLY_PROMPT_NAME = ai_generation.KNOWLEDGE_REPLY

COMPLETION_REQUEST_HINT = (
    "Write the reply in Swedish, and ask only for the fields listed below. "
    "Name each one in wording the customer will recognise."
)

GENERATED_COMPLETION_REQUEST = "generated_completion_request"

# What a customer calls the things the extraction reports as missing. The raw
# column names are the database's own vocabulary and must never reach a
# customer as English words. The keys are what the extraction really emits
# (order_extraction.EXTRACTION_FIELDS plus the customer the ticket resolves),
# and every name goes through `_()` so a site in another language can
# translate it; Swedish is the default wording, not the only one. Built per
# call rather than at import, because translation follows the request.
def _customer_field_names() -> dict:
    return {
        "customer": _("vilket företag ordern gäller"),
        "product": _("vilken produkt det gäller"),
        "quantity": _("antal"),
        "size": _("storlek"),
        "colors": _("färg"),
        "production_option": _("vilket utförande ni vill ha"),
        "delivery_information": _("leveransadress och önskat leveransdatum"),
        "original_files": _("tryckoriginal"),
    }


# A field nobody has named for the customer is still not shown as a column
# name; it is asked for as a detail of the order, which is true and readable.
UNNAMED_FIELD = "en uppgift om er order som saknas"


def _customer_names(missing: list) -> list:
    """Return what to call each missing field to the customer, no raw keys, no repeats."""
    names = _customer_field_names()
    named = []
    for field in missing:
        name = names.get(field) or _(UNNAMED_FIELD)
        if name not in named:
            named.append(name)
    return named

# The question type that marks a recurring question, and so selects the prompt
# tuned for one. Answering "hur lång är leveranstiden" is a different job from
# drafting a reply to a question nobody has asked before, and the two are tuned
# apart.
COMMON_QUESTION_TYPE = "common_question"

KNOWLEDGE_REPLY_INSTRUCTIONS = ai_generation.KNOWLEDGE_REPLY_INSTRUCTIONS


def _approved_knowledge(articles):
    """Return the citations and the engine context for one list of articles.

    Both halves are derived here from the single list that passed the approval
    filter, so no text the library has not released can reach the engine.
    """
    sources = [
        {
            "article": article.name,
            "title": article.title,
            "version": article.ai_approved_version,
        }
        for article in articles
    ]
    context = "\n\n".join(strip_html(article.content or "") for article in articles)
    return sources, context


def _working_language_hint(working):
    """Tell the engine to answer at home, whatever language the question came in.

    The library it is grounded in is written in the working language, and the
    reply is read and approved by an agent who works in it. An answer written
    straight into the customer's language is the model's own unreviewed
    translation of approved wording, which is exactly the thing the desk
    promises never to send.
    """
    return (
        f"Write the reply in the language with the code {working}, whatever "
        "language the question is written in. Do not translate it for the "
        "customer: an agent reads it in that language and a separate, reviewed "
        "step translates it afterwards."
    )


def _reply_messages(instructions, knowledge, question, working=None):
    """Show the engine its instructions and the approved knowledge, then the question."""
    system = f"{instructions}\n\nApproved knowledge:\n{knowledge}"
    if working:
        system = f"{system}\n\n{_working_language_hint(working)}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def _generated_reply(engines, knowledge, question, prompt_name, working=None):
    """Return the reply the chain wrote, together with its provenance.

    `engines` is the order to ask, resolved from the call's route the way every
    other generator does it (Wave 21); the first engine that answers wins, and
    the provenance names that engine rather than the one asked first.

    A failed generation is deliberately not caught: falling back to the raw
    knowledge extract would let an engine that never answered masquerade as one
    that did, so the failure surfaces before any draft exists.
    """
    instructions, prompt_version = ai_generation._prompt(prompt_name)
    response = ai_generation._answered(
        engines,
        lambda engine: ai_runner.generate(
            engine=engine,
            messages=_reply_messages(instructions, knowledge, question, working),
        ),
        prompt_name,
    )
    text = response.get("text")
    if not text:
        frappe.throw(_("The AI engine wrote no reply to draft from."))
    return {
        "body": text,
        "provider": response.get("provider"),
        "model_version": response.get("model"),
        "prompt_version": prompt_version,
        **ai_generation.usage_fields(response),
    }


def _extracted_reply(knowledge):
    """Return the knowledge extract used where no engine writes the reply."""
    return {
        "body": knowledge,
        "provider": None,
        "model_version": None,
        "prompt_version": None,
        **dict.fromkeys(ai_generation.COST_FIELDS),
    }


def _summed_costs(reply, generation):
    """Add what the repair cost to what the draft cost, in the record's fields.

    The repair is a second call on the same draft, so its tokens belong to the
    same row: a ticket's roll-up that counted only the first call would show a
    price nobody was charged. The cost is known only when both halves are, and
    the engine named stays the one that wrote the answer.
    """
    costs = {field: reply.get(field) for field in ai_generation.COST_FIELDS}
    for field in ai_generation.TOKEN_FIELDS:
        costs[field] = cint(costs.get(field)) + cint(generation.get(field))
    known = cint(reply.get("cost_known")) and cint(generation.get("cost_known"))
    costs["ai_cost"] = flt(reply.get("ai_cost")) + flt(generation.get("ai_cost"))
    costs["cost_known"] = 1 if known else 0
    return costs


def _brought_home(reply, knowledge, working):
    """Return a draft in the working language, whatever language the engine wrote.

    The instruction in the prompt is not a guarantee: a model that ignores it
    hands the agent an answer they may not read, and one nobody has checked
    against the Swedish articles it was drafted from. So the answer is verified
    with the same detector the thread uses, and only a foreign answer costs the
    repair — the ordinary case is one call, as before.

    When even the repair comes back foreign, the approved wording itself is
    what the agent gets. It is worse prose and it is the one text on the table
    that is both in the working language and actually approved; an unreviewed
    machine rendering of the library is precisely what this wave exists to
    keep off the agent's screen.
    """
    detected = translation.detect_language_code(reply["body"])
    if not detected or translation.same_language(detected, working):
        return reply

    try:
        text, generation = translation._generated_translation(
            reply["body"], detected, working, ai_generation.TRANSLATION_OUTBOUND
        )
    except Exception:
        text, generation = None, None
        frappe.log_error(
            title="Helpdesk AI reply",
            message="could not bring a foreign draft home\n\n"
            f"{frappe.get_traceback()}",
        )

    if text and translation.same_language(
        translation.detect_language_code(text), working
    ):
        return {**reply, "body": text, **_summed_costs(reply, generation)}

    if knowledge:
        return _extracted_reply(knowledge)
    return reply


def _apply_auto_reply_policy(doc):
    """Only question types an administrator released may skip human approval."""
    if not doc.question_type:
        return
    policy = frappe.db.get_value(
        "HD AI Reply Policy",
        {"question_type": doc.question_type, "enabled": 1, "auto_send": 1},
        ["minimum_confidence"],
        as_dict=True,
    )
    if not policy or flt(doc.confidence) < flt(policy.minimum_confidence):
        return
    doc.requires_approval = 0
    doc.status = "Approved"


@frappe.whitelist(methods=["POST"])
@agent_only
def record_reply_draft(
    ticket_id: str,
    body: str,
    question: str | None = None,
    question_type: str | None = None,
    language: str | None = None,
    sources: dict | list | str | None = None,
    confidence: float | int | None = 0,
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
    """Persist a proposed customer reply for human review, replayable by key."""
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    if idempotency_key:
        existing = frappe.db.get_value(
            "HD AI Reply Draft", {"idempotency_key": idempotency_key}, "name"
        )
        if existing:
            return frappe.get_doc("HD AI Reply Draft", existing).as_dict()
    if isinstance(sources, str):
        sources = json.loads(sources or "[]")
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
            "doctype": "HD AI Reply Draft",
            "ticket": ticket_id,
            "body": body,
            "question": question,
            "question_type": question_type,
            "language": language,
            "sources": json.dumps(sources or []),
            "confidence": flt(confidence),
            "status": "Draft",
            "requires_approval": 1,
            "provider": provider,
            "model_version": model_version,
            "prompt_version": prompt_version,
            "idempotency_key": idempotency_key,
            **costs,
        }
    )
    _apply_auto_reply_policy(doc)
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


def _cited_sources(draft: dict) -> list:
    """Return what a stored draft rests on, as rows the editor can render.

    `record_reply_draft` keeps the sources; this is only a view of them, so
    the suggestion and `get_reply_sources` can never disagree about what the
    answer was drafted from.
    """
    sources = draft.get("sources") or []
    if isinstance(sources, str):
        sources = json.loads(sources or "[]")
    rows = []
    for source in sources:
        row = {
            "article": source.get("article") or source.get("name"),
            "title": source.get("title"),
        }
        # A draft that recorded which version it read keeps saying so, so the
        # editor can show the same staleness get_reply_sources computes.
        for stamp in ("version", "modified"):
            if source.get(stamp) is not None:
                row[stamp] = source.get(stamp)
        rows.append(row)
    return rows


def _with_sources(draft: dict) -> dict:
    """Hand the editor the draft and its citations in one answer."""
    answer = dict(draft)
    answer["sources"] = _cited_sources(draft)
    return answer


def _reply_prompt_name(question_type: str | None) -> str:
    """Return the prompt tuned for the job this draft is doing."""
    if question_type == COMMON_QUESTION_TYPE:
        return ai_generation.COMMON_QUESTION
    return ai_generation.KNOWLEDGE_REPLY


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_knowledge_reply(
    ticket_id: str,
    question: str,
    question_type: str | None = None,
    category: str | None = None,
    language: str | None = None,
    idempotency_key: str | None = None,
    limit: int = 3,
) -> dict:
    """Draft a reply grounded in the approved knowledge library."""
    articles = search_knowledge(question, limit=limit, category=category)
    return _draft_from_articles(
        ticket_id, question, articles, question_type, language, idempotency_key
    )


def _confidence(articles) -> float:
    """How well the best source covers the question, 0 to 1.

    "Had a source" is not a quality: a policy that auto-sends above a
    threshold (Wave 25) must be weighing how much of the question the article
    actually covers, which is the relevance the search ranks by. A row that
    reports none is taken at face value as a full match, the old reading.
    """
    if not articles:
        return 0
    return max(flt(article.get("relevance", 1)) for article in articles)


def _draft_from_articles(
    ticket_id, question, articles, question_type, language, idempotency_key
) -> dict:
    """Draft from articles already searched, so one suggestion searches once."""
    sources, knowledge = _approved_knowledge(articles)
    # The call's own route, not the bare default: a knowledge reply or a
    # common question follows the order the administrator wrote for it, and
    # walks the chain like every other caller. Without approved sources or a
    # runner there is nothing to generate from, and the extract stands as
    # before.
    prompt_name = _reply_prompt_name(question_type)
    engines = (
        ai_generation.engines_or_throw(prompt_name)
        if sources and ai_runner.is_runner_available()
        else None
    )
    working = translation.get_working_language()
    reply = (
        _brought_home(
            _generated_reply(engines, knowledge, question, prompt_name, working),
            knowledge,
            working,
        )
        if engines
        else _extracted_reply(knowledge)
    )
    draft = record_reply_draft(
        ticket_id=ticket_id,
        body=reply["body"],
        question=question,
        question_type=question_type,
        language=language,
        sources=sources,
        confidence=_confidence(articles),
        provider=reply["provider"],
        model_version=reply["model_version"],
        prompt_version=reply["prompt_version"],
        idempotency_key=idempotency_key,
        **{field: reply.get(field) for field in ai_generation.COST_FIELDS},
    )
    if reply["provider"]:
        ai_generation.attribute(
            "drafted a reply from the knowledge library",
            "HD AI Reply Draft",
            draft["name"],
            {
                "provider": reply["provider"],
                "model_version": reply["model_version"],
                "prompt_version": reply["prompt_version"],
            },
        )
    return draft


@frappe.whitelist()
@agent_only
def get_reply_sources(draft_id: str) -> list:
    """Return the knowledge a draft was based on, flagging superseded versions."""
    doc = frappe.get_doc("HD AI Reply Draft", draft_id)
    sources = doc.sources or []
    if isinstance(sources, str):
        sources = json.loads(sources or "[]")
    resolved = []
    for source in sources:
        current = (
            frappe.db.get_value(
                "HD Article",
                source.get("article"),
                ["title", "ai_approved_version"],
                as_dict=True,
            )
            or {}
        )
        resolved.append(
            {
                "article": source.get("article"),
                "title": current.get("title") or source.get("title"),
                "cited_version": source.get("version"),
                "current_version": current.get("ai_approved_version"),
                "stale": cint(source.get("version"))
                != cint(current.get("ai_approved_version")),
            }
        )
    return resolved


@frappe.whitelist(methods=["POST"])
@agent_only
def approve_reply_draft(draft_id: str) -> dict:
    """Record the human approval a drafted reply needs before it is sent."""
    doc = frappe.get_doc("HD AI Reply Draft", draft_id)
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def send_reply_draft(draft_id: str) -> dict:
    """Send an approved draft; an unapproved draft must never reach the customer."""
    doc = frappe.get_doc("HD AI Reply Draft", draft_id)
    if doc.status != "Approved":
        frappe.throw(_("This reply must be approved before it is sent."))
    doc.status = "Sent"
    doc.sent_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def answer_common_question(
    ticket_id: str,
    question: str,
    category: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Answer a recurring question such as delivery times or product facts."""
    return draft_knowledge_reply(
        ticket_id=ticket_id,
        question=question,
        question_type=COMMON_QUESTION_TYPE,
        category=category,
        idempotency_key=idempotency_key,
    )


def _missing_order_fields(extraction_id):
    """Return what one extraction still lacks, refusing an order that lacks nothing."""
    extraction = frappe.get_doc("HD Order Extraction", extraction_id)
    missing = extraction.missing_fields
    if isinstance(missing, str):
        missing = json.loads(missing or "[]")
    if not missing:
        frappe.throw(_("This order is not missing any information."))
    return extraction, missing


@frappe.whitelist(methods=["POST"])
@agent_only
def generate_completion_request(
    extraction_id: str, idempotency_key: str | None = None
) -> dict:
    """Have the AI engine write the reply asking for the missing order details.

    Only the fields the extraction actually lacks are shown to the engine, so
    the customer is asked for what is missing and nothing more. The fixed
    wording of `draft_completion_request` stays available for a helpdesk that
    runs without an engine.

    Free-form engine output is its own question type. An administrator who
    released the fixed wording for auto-sending released wording they have
    read; releasing whatever the model writes next is a separate decision, and
    the generation reports no confidence of its own to weigh against a policy.
    """
    stored = ai_generation.replayed("HD AI Reply Draft", idempotency_key)
    if stored:
        return frappe.get_doc("HD AI Reply Draft", stored).as_dict()
    extraction, missing = _missing_order_fields(extraction_id)
    engines = ai_generation.engines_or_throw(ai_generation.COMPLETION_REQUEST)
    instructions, prompt_version = ai_generation._prompt(
        ai_generation.COMPLETION_REQUEST
    )
    # The engine is handed each field the way the customer would name it, next
    # to the key the extraction uses, so it asks for "antal" and not "quantity".
    named = zip(missing, _customer_names(missing))
    body, response = ai_generation.generate_text(
        engines,
        instructions,
        "\n".join(f"{field}: {name}" for field, name in named),
        COMPLETION_REQUEST_HINT,
        call=ai_generation.COMPLETION_REQUEST,
    )
    generation = ai_generation.provenance(response, prompt_version)
    draft = record_reply_draft(
        ticket_id=extraction.ticket,
        body=body,
        question_type=GENERATED_COMPLETION_REQUEST,
        sources=[],
        confidence=0,
        idempotency_key=idempotency_key,
        **generation,
    )
    ai_generation.attribute(
        "wrote a completion request",
        "HD AI Reply Draft",
        draft["name"],
        generation,
    )
    return draft


def _completion_request_body(missing: list) -> str:
    """Ask, in prose, for what the order still lacks.

    The customer reads this text, so it names the things the way they would:
    no field names, no parentheses and no bullet list of column names. The
    things are listed as nouns after a colon rather than chained as indirect
    questions, so five missing fields still read as one sentence.

    An empty list produces no sentence: there is nothing to ask for, and the
    callers refuse such an extraction before they get here
    (`_missing_order_fields`), so an empty string here is a guard, not a reply.
    """
    named = _customer_names(missing)
    if not named:
        return ""
    if len(named) == 1:
        asked = named[0]
    else:
        asked = ", ".join(named[:-1]) + " " + _("och") + " " + named[-1]
    return _(
        "Hej! För att vi ska kunna gå vidare med er order behöver vi följande "
        "uppgifter: {asked}. Svara gärna på det här mejlet så sätter vi igång "
        "så snart vi har dem."
    ).format(asked=asked)


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_completion_request(
    extraction_id: str, idempotency_key: str | None = None
) -> dict:
    """Draft the reply that asks a customer for the order details still missing."""
    extraction, missing = _missing_order_fields(extraction_id)
    return record_reply_draft(
        ticket_id=extraction.ticket,
        body=_completion_request_body(missing),
        question_type="completion_request",
        sources=[],
        confidence=1,
        idempotency_key=idempotency_key,
    )


def _newest_extraction(ticket_id: str):
    """Return the ticket's most recent extraction as a document, or None."""
    name = frappe.db.get_value(
        "HD Order Extraction",
        {"ticket": ticket_id},
        "name",
        order_by="creation desc",
    )
    return frappe.get_doc("HD Order Extraction", name) if name else None


def _missing_of(extraction) -> list:
    missing = extraction.missing_fields
    if isinstance(missing, str):
        missing = json.loads(missing or "[]")
    return missing or []


@frappe.whitelist(methods=["POST"])
@agent_only
def suggest_reply(ticket_id: str) -> dict:
    """Draft the one reply this ticket needs and hand it to the editor.

    An order that still lacks details gets the fixed-wording completion
    request naming only those details, never the engine-generated variant:
    the released wording is what the auto-reply policy already covers, and
    an administrator who released it released text they have read. Any other
    ticket gets a reply grounded in the approved knowledge library when the
    library has one. Nothing is sent: the result is a draft the agent edits
    before sending, or a reason why there is none.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    extraction = _newest_extraction(ticket_id)
    if extraction and _missing_of(extraction):
        return _with_sources(draft_completion_request(extraction.name))

    # One search: the same rows gate the suggestion and ground it, rather
    # than a scan to decide and a second scan to draft.
    question = ai_generation.ticket_text(ticket_id)
    articles = search_knowledge(question, limit=3) if question else []
    if articles:
        return _with_sources(
            _draft_from_articles(ticket_id, question, articles, None, None, None)
        )

    return {
        "body": None,
        "sources": [],
        "reason": _(
            "Inget förslag: ordern saknar inga uppgifter och kunskapsbiblioteket "
            "har inget godkänt svar på frågan."
        ),
    }
