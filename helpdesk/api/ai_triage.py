import json

import frappe
from frappe.utils import flt, now_datetime

from helpdesk.api import ai_generation
from helpdesk.utils import agent_only

TRIAGE_SCHEMA = (
    "Use these keys and no others: classification (what the message is about), "
    "priority (Low, Medium, High or Urgent), suggested_agent (an email "
    "address), confidence (0 to 1), rationale, summary, missing_information, "
    "complaint (true or false), repeat_order (true or false)."
)

TRIAGE_FIELDS = (
    "classification",
    "priority",
    "suggested_agent",
    "confidence",
    "rationale",
    "summary",
    "missing_information",
    "complaint",
    "repeat_order",
)

TRIAGE_LINKS = {"priority": "HD Ticket Priority", "suggested_agent": "User"}

DEFAULT_CONFIDENCE_THRESHOLD = 0.7


def _linked_values(proposal: dict) -> dict:
    """Drop a link the model invented, and keep everything it got right.

    `priority` and `suggested_agent` point at records that have to exist. A
    value naming no record would make the whole proposal unsaveable, so it is
    dropped the way an unknown key already is: one field the model could not
    supply is worth far less than the triage it would otherwise take down.
    """
    return {
        field: value
        for field, value in proposal.items()
        if field not in TRIAGE_LINKS
        or not value
        or frappe.db.exists(TRIAGE_LINKS[field], value)
    }


@frappe.whitelist(methods=["POST"])
@agent_only
def record_triage(
    ticket_id: str,
    classification: str | None = None,
    priority: str | None = None,
    suggested_agent: str | None = None,
    confidence: float | int | None = 0,
    confidence_threshold: float | int | None = 0,
    rationale: str | None = None,
    action: str = "propose",
    provider: str | None = None,
    model_version: str | None = None,
    prompt_version: str | int | None = None,
    idempotency_key: str | None = None,
    missing_information: str | None = None,
    attachment_assessment: str | None = None,
    repeat_order: int | bool | None = 0,
    complaint: int | bool | None = 0,
    summary: str | None = None,
    source_message: str | None = None,
    repeat_order_evidence: str | None = None,
    complaint_evidence: str | None = None,
    action_thresholds: dict | list | str | None = None,
    failed_action: str | None = None,
    ticket_completed: int | bool | None = 0,
) -> dict:
    """Persist a reviewable triage proposal, safely replayable by key."""
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    if idempotency_key:
        existing = frappe.db.get_value("HD AI Triage Result", {"idempotency_key": idempotency_key}, "name")
        if existing:
            return frappe.get_doc("HD AI Triage Result", existing).as_dict()
    confidence = flt(confidence)
    if isinstance(action_thresholds, str):
        action_thresholds = json.loads(action_thresholds or "{}")
    action_thresholds = action_thresholds or {}
    confidence_threshold = flt(action_thresholds.get(action, confidence_threshold))
    needs_review = confidence < confidence_threshold
    if failed_action:
        needs_review = True
    doc = frappe.get_doc({
        "doctype": "HD AI Triage Result", "ticket": ticket_id,
        "classification": classification, "priority": priority,
        "suggested_agent": suggested_agent, "confidence": confidence,
        "confidence_threshold": confidence_threshold,
        "requires_human_review": needs_review,
        "status": "Automation Failed" if failed_action else ("Needs Review" if needs_review else "Proposed"),
        "action": "manual_review" if needs_review else action,
        "source_message": source_message,
        "rationale": rationale, "provider": provider,
        "model_version": model_version, "prompt_version": prompt_version,
        "idempotency_key": idempotency_key,
        "missing_information": missing_information,
        "attachment_assessment": attachment_assessment,
        "repeat_order": repeat_order, "complaint": complaint, "summary": summary,
        "repeat_order_evidence": repeat_order_evidence,
        "complaint_evidence": complaint_evidence,
        "action_thresholds": action_thresholds,
        "failed_action": failed_action,
        "ticket_completed": 0 if failed_action else ticket_completed,
        "audit_timestamp": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def triage_ticket(
    ticket_id: str,
    idempotency_key: str | None = None,
    confidence_threshold: float | int | None = None,
) -> dict:
    """Have the AI engine triage one ticket, and record its proposal for review.

    Only the fields the triage record knows are taken from the answer, and
    only those the engine actually returned: a key it left out stays unset,
    because "the model did not say" is the honest recording of it.

    How sure the model claims to be decides nothing on its own. The proposal is
    measured against a threshold here exactly as a hand-recorded one is, so a
    hesitant answer reaches an agent instead of standing as a settled triage.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    stored = ai_generation.replayed("HD AI Triage Result", idempotency_key)
    if stored:
        return frappe.get_doc("HD AI Triage Result", stored).as_dict()
    engine = ai_generation.engine_or_throw()
    instructions, prompt_version = ai_generation._prompt(ai_generation.TICKET_TRIAGE)
    answer, response = ai_generation.generate_json(
        engine,
        instructions,
        ai_generation.ticket_text(ticket_id),
        TRIAGE_SCHEMA,
        TRIAGE_FIELDS,
    )
    proposal = _linked_values(
        {field: answer[field] for field in TRIAGE_FIELDS if field in answer}
    )
    if confidence_threshold is None:
        confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
    return record_triage(
        ticket_id=ticket_id,
        idempotency_key=idempotency_key,
        confidence_threshold=confidence_threshold,
        **ai_generation.provenance(response, prompt_version),
        **proposal,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def correct_triage(
    triage_id: str, classification: str, reason: str | None = None
) -> dict:
    """Record a human correction without erasing the original proposal."""
    doc = frappe.get_doc("HD AI Triage Result", triage_id)
    doc.corrected_classification = classification
    doc.correction_reason = reason
    doc.corrected_by = frappe.session.user
    doc.corrected_on = now_datetime()
    doc.status = "Corrected"
    doc.save(ignore_permissions=True)
    return doc.as_dict()
