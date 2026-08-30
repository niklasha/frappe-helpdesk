import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, strip_html

from helpdesk.api import ai_engine, ai_runner
from helpdesk.api.knowledge_library import search_knowledge
from helpdesk.utils import agent_only

KNOWLEDGE_REPLY_PROMPT_NAME = "knowledge_reply"

KNOWLEDGE_REPLY_INSTRUCTIONS = (
    "Answer the customer question using only the approved knowledge below. "
    "When the knowledge does not cover the question, say so plainly instead of "
    "guessing, and offer to pass the question to a colleague."
)


def _reply_instructions():
    """Return the reply instructions and the prompt version they came from.

    An administrator owns what the AI is told, so the prompt library wins over
    the built-in wording whenever it holds an enabled prompt.
    """
    prompt = frappe.db.get_value(
        "HD AI Prompt",
        {"prompt_name": KNOWLEDGE_REPLY_PROMPT_NAME, "enabled": 1},
        ["prompt", "version"],
        as_dict=True,
    )
    if prompt and prompt.prompt:
        return prompt.prompt, prompt.version
    return KNOWLEDGE_REPLY_INSTRUCTIONS, None


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


def _generated_reply(engine, knowledge, question):
    """Return the reply the engine wrote, together with its provenance.

    A failed generation is deliberately not caught: falling back to the raw
    knowledge extract would let an engine that never answered masquerade as one
    that did, so the failure surfaces before any draft exists.
    """
    instructions, prompt_version = _reply_instructions()
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
    }


def _extracted_reply(knowledge):
    """Return the knowledge extract used where no engine writes the reply."""
    return {
        "body": knowledge,
        "provider": None,
        "model_version": None,
        "prompt_version": None,
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
    ticket_id,
    body,
    question=None,
    question_type=None,
    language=None,
    sources=None,
    confidence=0,
    provider=None,
    model_version=None,
    prompt_version=None,
    idempotency_key=None,
):
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
        }
    )
    _apply_auto_reply_policy(doc)
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_knowledge_reply(
    ticket_id,
    question,
    question_type=None,
    category=None,
    language=None,
    idempotency_key=None,
    limit=3,
):
    """Draft a reply grounded in the approved knowledge library."""
    articles = search_knowledge(question, limit=limit, category=category)
    sources, knowledge = _approved_knowledge(articles)
    engine = (
        ai_engine.default_engine()
        if sources and ai_runner.is_runner_available()
        else None
    )
    reply = (
        _generated_reply(engine, knowledge, question)
        if engine
        else _extracted_reply(knowledge)
    )
    return record_reply_draft(
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
    )


@frappe.whitelist()
@agent_only
def get_reply_sources(draft_id):
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
def approve_reply_draft(draft_id):
    """Record the human approval a drafted reply needs before it is sent."""
    doc = frappe.get_doc("HD AI Reply Draft", draft_id)
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def send_reply_draft(draft_id):
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
def answer_common_question(ticket_id, question, category=None, idempotency_key=None):
    """Answer a recurring question such as delivery times or product facts."""
    return draft_knowledge_reply(
        ticket_id=ticket_id,
        question=question,
        question_type="common_question",
        category=category,
        idempotency_key=idempotency_key,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def draft_completion_request(extraction_id, idempotency_key=None):
    """Draft the reply that asks a customer for the order details still missing."""
    extraction = frappe.get_doc("HD Order Extraction", extraction_id)
    missing = extraction.missing_fields
    if isinstance(missing, str):
        missing = json.loads(missing or "[]")
    if not missing:
        frappe.throw(_("This order is not missing any information."))
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
