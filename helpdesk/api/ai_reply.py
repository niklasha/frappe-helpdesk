import json

import frappe
from frappe.utils import flt

from helpdesk.utils import agent_only


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
    doc.insert(ignore_permissions=True)
    return doc.as_dict()
