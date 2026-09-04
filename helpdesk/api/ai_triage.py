import json

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from helpdesk.api import ai_generation, governance
from helpdesk.helpdesk.doctype.hd_ticket_type.hd_ticket_type import (
    classification_group,
    match_ticket_type,
)
from helpdesk.utils import agent_only

TRIAGE_SCHEMA = (
    "Use these keys and no others: classification (what the message is about), "
    "priority (Low, Medium, High or Urgent), suggested_agent (an email "
    "address), confidence (0 to 1), rationale, summary, missing_information "
    "(one sentence naming what the customer has not told us, not a list), "
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

# How many catalogue labels the prompt is allowed to list. The lookup that
# reads the catalogue is explicitly unbounded (limit_page_length=0, since
# Frappe's default page is 20 and the desk alone has that many), so the cap is
# set here, on purpose and in one place: a site that has grown hundreds of
# types would otherwise spend the prompt on its own vocabulary and push the
# customer's message out of the model's attention. Past the cap the model is
# still asked to answer with a catalogue label, it is just not shown them all,
# and an answer outside the catalogue still resolves to nothing rather than to
# a guess.
CATALOGUE_PROMPT_CAP = 200


def _catalogue_sentence() -> str:
    """One sentence naming the enabled ticket types, or nothing.

    Nothing when the catalogue is empty, so a fresh site's prompt does not ask
    the model to choose from an empty list.
    """
    rows = frappe.get_all(
        "HD Ticket Type",
        filters={"disabled": 0},
        pluck="name",
        order_by="name asc",
        limit_page_length=0,
    )
    names = [name for name in rows if name][:CATALOGUE_PROMPT_CAP]
    if not names:
        return ""
    return (
        " For classification, answer with exactly one of these labels, spelled "
        "as written: " + ", ".join(names) + "."
    )


def _schema_with_catalogue() -> str:
    """The triage schema with the desk's own vocabulary appended.

    TRIAGE_SCHEMA itself stays a constant: what the model is asked for does not
    change per site, only which labels it may answer with.
    """
    return TRIAGE_SCHEMA + _catalogue_sentence()


# Fields that are prose in the record and that a model readily answers with a
# list instead — reasonably, since "what is missing" is naturally several things.
PROSE_FIELDS = ("missing_information", "rationale", "summary", "classification")


def _as_prose(value):
    """Flatten a listed answer into the sentence the record can hold.

    Frappe refuses a list for a Small Text field outright, and the refusal takes
    the whole triage down — the ticket ends up with no triage at all rather than
    one field in an awkward shape. Since what the model listed is exactly what an
    agent has to go and ask the customer for, none of it may be dropped either.

    The schema now asks for a sentence, which makes this rare. It does not make
    it impossible: the answer is a document somebody else wrote.
    """
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts) or None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value


def _prose_values(proposal: dict) -> dict:
    """Put every prose field into a shape its own field can store."""
    return {
        field: _as_prose(value) if field in PROSE_FIELDS else value
        for field, value in proposal.items()
    }


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
        "classification": classification,
        # The model's words are kept as written; beside them, the catalogued
        # type those words name — or nothing when the catalogue holds no such
        # label. Only recorded here: the ticket's own type moves when an agent
        # accepts, never on a proposal.
        "proposed_ticket_type": match_ticket_type(classification),
        "priority": priority,
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
        _schema_with_catalogue(),
        TRIAGE_FIELDS,
    )
    proposal = _linked_values(
        _prose_values({field: answer[field] for field in TRIAGE_FIELDS if field in answer})
    )
    if confidence_threshold is None:
        confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
    generation = ai_generation.provenance(response, prompt_version)
    result = record_triage(
        ticket_id=ticket_id,
        idempotency_key=idempotency_key,
        confidence_threshold=confidence_threshold,
        **generation,
        **proposal,
    )
    ai_generation.attribute(
        "triaged a ticket", "HD AI Triage Result", result["name"], generation
    )
    return result


@frappe.whitelist(methods=["POST"])
@agent_only
def correct_triage(
    triage_id: str, classification: str, reason: str | None = None
) -> dict:
    """Record a human correction without erasing the original proposal.

    The corrected label resolves against the catalogue the same way the
    model's answer did, so a correction is as catalogued as a proposal.
    """
    doc = frappe.get_doc("HD AI Triage Result", triage_id)
    doc.corrected_classification = classification
    doc.corrected_ticket_type = match_ticket_type(classification)
    doc.correction_reason = reason
    doc.corrected_by = frappe.session.user
    doc.corrected_on = now_datetime()
    # A correction after an acceptance is a change of mind. Left in place, the
    # acceptance made the row say two things at once — that the proposal was
    # applied, and that it was wrong — and the header went on showing
    # "Godtagen av …" over a type nobody stands behind. Clearing it makes the
    # row read as a fresh proposal, which accept_triage can apply again.
    doc.accepted_by = None
    doc.accepted_on = None
    doc.applied_fields = None
    doc.refused_fields = None
    doc.status = "Corrected"
    doc.save(ignore_permissions=True)
    return doc.as_dict()


# The ticket fields one click may write. agent_group is deliberately absent:
# writing it is a routing action, not a field write. HD Ticket.on_update calls
# remove_assignment_if_not_in_team, which clears every assignment when the
# team changes on an Open ticket whose assignee is not in the new team and
# whose new team's Assignment Rule has users, and Frappe's rule then hands the
# ticket to that rotation. ROUTE-10 and K8 say the person on the case stays on
# it, so the team is derived and returned (suggested_team), never applied.
ACCEPTED_FIELDS = ("ticket_type", "priority")


def _derived_team(agent: str | None) -> str | None:
    """The one team the suggested agent belongs to, or nothing.

    Read-only: nothing here writes to the ticket. An agent in no team or in
    several has no single team to derive, and guessing would be worse than
    saying so — the caller records the refusal in words instead.
    """
    if not agent:
        return None
    teams = frappe.get_all("HD Team Member", filters={"user": agent}, pluck="parent")
    teams = sorted(set(teams))
    return teams[0] if len(teams) == 1 else None


def _acceptance(doc) -> tuple[dict, dict]:
    """(values to apply, refusals in words) for one proposal.

    The type applied is the Link resolved when the proposal was recorded or
    corrected — never the free-text classification, which would fail the Link
    validation or, worse, create the type. It is resolved again here, at accept
    time: a type disabled since the proposal was written must not be written
    to the ticket on the strength of a lookup that was true last week. A
    proposal that resolves to no type has nothing to write and says so; the
    priority is a separate value and still applies, so one refused field never
    takes the other down with it.
    """
    applied, refused = {}, {}
    ticket_type = match_ticket_type(doc.corrected_ticket_type or doc.proposed_ticket_type)
    if ticket_type:
        applied["ticket_type"] = ticket_type
    else:
        label = doc.corrected_classification or doc.classification or ""
        refused["ticket_type"] = _("Ärendetypen {0} finns inte i katalogen").format(
            f"'{label}'" if label else _("(ingen)")
        )
    if doc.priority and frappe.db.exists("HD Ticket Priority", doc.priority):
        applied["priority"] = doc.priority
    elif doc.priority:
        refused["priority"] = _("Prioriteten {0} finns inte längre").format(f"'{doc.priority}'")
    if doc.suggested_agent and not _derived_team(doc.suggested_agent):
        # Recorded so the panel can say "hör till flera team" rather than
        # saying nothing about a derivation that declined.
        refused["agent_group"] = _("{0} hör till flera team eller inget").format(
            doc.suggested_agent
        )
    return applied, refused


@frappe.whitelist(methods=["POST"])
@agent_only
def accept_triage(triage_id: str) -> dict:
    """Apply one proposal to its ticket, and sign the proposal with who did it.

    Only ACCEPTED_FIELDS are written, through ticket.save() so every rule the
    ticket carries (SLA, priority from type, activity) runs as it would for a
    hand edit — db.set_value would leave the ticket typed but its SLA unaware.
    The team is returned as suggested_team and never written: see
    ACCEPTED_FIELDS for why.
    """
    doc = frappe.get_doc("HD AI Triage Result", triage_id)
    frappe.has_permission("HD Ticket", "write", doc=doc.ticket, throw=True)
    applied, refused = _acceptance(doc)
    if applied:
        ticket = frappe.get_doc("HD Ticket", doc.ticket)
        for field, value in applied.items():
            ticket.set(field, value)
        # HD Ticket.set_classification_model returns early for an existing
        # ticket, so the coarse class would stay where the first classification
        # left it while the type moved on. Roll the type up here; a type that
        # states no group says nothing and leaves the class alone.
        if "ticket_type" in applied:
            group = classification_group(applied["ticket_type"])
            if group and group != ticket.classification_model:
                ticket.classification_model = group
                applied["classification_model"] = group
        ticket.save()
    doc.accepted_by = frappe.session.user
    doc.accepted_on = now_datetime()
    doc.applied_fields = ", ".join(applied) or None
    doc.refused_fields = json.dumps(refused, ensure_ascii=False) if refused else None
    doc.status = "Accepted"
    doc.save(ignore_permissions=True)
    governance.log_automation_event(
        "accept_triage",
        actor="User",
        reference_doctype="HD Ticket",
        reference_name=doc.ticket,
        details={"triage": doc.name, "applied": applied, "refused": refused},
    )
    answer = doc.as_dict()
    answer["applied"] = applied
    answer["refused"] = refused
    answer["suggested_team"] = _derived_team(doc.suggested_agent)
    return answer


# What a panel needs to show one proposal. Named here rather than in the
# component, so the next reader of this record does not invent a slightly
# different list and a slightly different ordering.
TRIAGE_VIEW_FIELDS = (
    "name",
    "ticket",
    "classification",
    "proposed_ticket_type",
    "priority",
    "suggested_agent",
    "confidence",
    "confidence_threshold",
    "requires_human_review",
    "status",
    "action",
    "rationale",
    "summary",
    "missing_information",
    "repeat_order",
    "complaint",
    "corrected_classification",
    "corrected_ticket_type",
    "correction_reason",
    "corrected_by",
    "corrected_on",
    "accepted_by",
    "accepted_on",
    "applied_fields",
    "refused_fields",
    "provider",
    "model_version",
    "prompt_version",
    "audit_timestamp",
)


@frappe.whitelist()
@agent_only
def ticket_triage(ticket_id: str) -> dict | None:
    """Return the AI's standing proposal for one ticket, or nothing.

    Nothing is an answer rather than an error: most tickets have no triage, and
    a panel that has to catch an exception to render an empty state is a panel
    that will one day render a stack trace.

    The newest wins. A ticket can accumulate proposals — an agent re-running the
    chain, a correction recorded beside the original — and the one an agent is
    working against is the last one made.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    rows = frappe.get_all(
        "HD AI Triage Result",
        filters={"ticket": ticket_id},
        fields=list(TRIAGE_VIEW_FIELDS),
        order_by="creation desc",
        limit_page_length=1,
    )
    return rows[0] if rows else None
