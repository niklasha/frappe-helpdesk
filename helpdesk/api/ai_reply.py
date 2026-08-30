import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, strip_html

from helpdesk.api.knowledge_library import search_knowledge
from helpdesk.utils import agent_only


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
    sources = [
        {
            "article": article.name,
            "title": article.title,
            "version": article.ai_approved_version,
        }
        for article in articles
    ]
    body = "\n\n".join(strip_html(article.content or "") for article in articles)
    return record_reply_draft(
        ticket_id=ticket_id,
        body=body,
        question=question,
        question_type=question_type,
        language=language,
        sources=sources,
        confidence=1 if sources else 0,
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
