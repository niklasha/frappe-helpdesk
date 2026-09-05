import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, strip_html

from helpdesk.api import ai_engine, ai_generation, ai_runner
from helpdesk.api.knowledge_library import search_knowledge
from helpdesk.utils import agent_only

KNOWLEDGE_REPLY_PROMPT_NAME = ai_generation.KNOWLEDGE_REPLY

COMPLETION_REQUEST_HINT = (
    "Write the reply in Swedish, and ask only for the fields listed below. "
    "Name each one in wording the customer will recognise."
)

GENERATED_COMPLETION_REQUEST = "generated_completion_request"

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


def _reply_messages(instructions, knowledge, question):
    """Show the engine its instructions and the approved knowledge, then the question."""
    return [
        {
            "role": "system",
            "content": f"{instructions}\n\nApproved knowledge:\n{knowledge}",
        },
        {"role": "user", "content": question},
    ]


def _generated_reply(engine, knowledge, question, prompt_name):
    """Return the reply the engine wrote, together with its provenance.

    A failed generation is deliberately not caught: falling back to the raw
    knowledge extract would let an engine that never answered masquerade as one
    that did, so the failure surfaces before any draft exists.
    """
    instructions, prompt_version = ai_generation._prompt(prompt_name)
    response = ai_runner.generate(
        engine=engine, messages=_reply_messages(instructions, knowledge, question)
    )
    text = response.get("text")
    if not text:
        frappe.throw(_("The AI engine wrote no reply to draft from."))
    return {
        "body": text,
        "provider": response.get("provider"),
        "model_version": response.get("model"),
        "prompt_version": prompt_version,
        **ai_generation.usage_fields(response, engine),
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
    sources, knowledge = _approved_knowledge(articles)
    engine = (
        ai_engine.default_engine()
        if sources and ai_runner.is_runner_available()
        else None
    )
    reply = (
        _generated_reply(engine, knowledge, question, _reply_prompt_name(question_type))
        if engine
        else _extracted_reply(knowledge)
    )
    draft = record_reply_draft(
        ticket_id=ticket_id,
        body=reply["body"],
        question=question,
        question_type=question_type,
        language=language,
        sources=sources,
        confidence=1 if sources else 0,
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
    engine = ai_generation.engine_or_throw()
    instructions, prompt_version = ai_generation._prompt(
        ai_generation.COMPLETION_REQUEST
    )
    body, response = ai_generation.generate_text(
        engine, instructions, ", ".join(missing), COMPLETION_REQUEST_HINT
    )
    generation = ai_generation.provenance(response, prompt_version, engine)
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


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_completion_request(
    extraction_id: str, idempotency_key: str | None = None
) -> dict:
    """Draft the reply that asks a customer for the order details still missing."""
    extraction, missing = _missing_order_fields(extraction_id)
    return record_reply_draft(
        ticket_id=extraction.ticket,
        body=_("To continue with your order we still need: {0}").format(
            ", ".join(missing)
        ),
        question_type="completion_request",
        sources=[],
        confidence=1,
        idempotency_key=idempotency_key,
    )
