import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from helpdesk.integrations.order import get_order_connector
from helpdesk.utils import agent_only

MANUAL_HANDLING_AFTER = 3
QUEUED_STATUSES = ("Failed", "Needs Manual Handling")


@frappe.whitelist(methods=["POST"])
@agent_only
def record_submission(ticket_id, extraction=None, idempotency_key=None, automated=0):
    """Record one attempt to create an order in an external system.

    The submission starts out ``Pending``; the connector result is applied
    afterwards, so Helpdesk owns the record even when no ERP answers. An
    idempotency key belongs to the ticket it was first recorded for, so
    reusing someone else's key is refused rather than answered with their
    submission.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    if idempotency_key:
        existing = frappe.db.get_value(
            "HD External Order Submission",
            {"idempotency_key": idempotency_key},
            ["name", "ticket"],
            as_dict=True,
        )
        if existing:
            if str(existing.ticket) != str(ticket_id):
                frappe.throw(
                    _("Idempotency key {0} already belongs to another ticket.").format(
                        idempotency_key
                    )
                )
            return frappe.get_doc("HD External Order Submission", existing.name).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD External Order Submission",
            "ticket": ticket_id,
            "extraction": extraction,
            "status": "Pending",
            "attempts": 0,
            "automated": cint(automated),
            "submitted_by": frappe.session.user,
            "submitted_on": now_datetime(),
            "idempotency_key": idempotency_key,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


def _ensure_external_link(ticket, link_type, target, label=None):
    """Attach an external reference to a ticket once, never twice."""
    if not target:
        return None
    existing = frappe.db.get_value(
        "HD Ticket External Link",
        {"ticket": ticket, "link_type": link_type, "target": target},
        "name",
    )
    if existing:
        return frappe.get_doc("HD Ticket External Link", existing).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD Ticket External Link",
            "ticket": ticket,
            "link_type": link_type,
            "target": target,
            "label": label,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def apply_submission_result(submission_id, external_order_id=None, error=None):
    """Record what the external system answered for one submission.

    A created order is linked back to its ticket so the agent can reach it;
    a refusal is kept with its error so the attempt can be repeated, and once
    the external system has refused it three times a person has to take over.
    """
    doc = frappe.get_doc("HD External Order Submission", submission_id)
    if error:
        doc.attempts = cint(doc.attempts) + 1
        doc.last_error = error
        doc.status = "Failed" if doc.attempts < MANUAL_HANDLING_AFTER else "Needs Manual Handling"
    else:
        doc.status = "Submitted"
        doc.external_order_id = external_order_id
    doc.save(ignore_permissions=True)
    if doc.status == "Submitted":
        _ensure_external_link(doc.ticket, "order", doc.external_order_id, _("Order"))
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def link_order_relationships(ticket_id, links):
    """Relate a ticket to its orders, proofs, corrections and original files.

    Every relation is stored once per ticket, link type and target, so replaying
    the same set of links leaves the ticket unchanged.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    if isinstance(links, str):
        links = json.loads(links or "[]")
    for link in links:
        _ensure_external_link(
            ticket_id, link.get("link_type"), link.get("target"), link.get("label")
        )
    return frappe.get_all(
        "HD Ticket External Link",
        filters={"ticket": ticket_id},
        fields=["name", "ticket", "link_type", "target", "label"],
        order_by="creation asc",
    )


def _open_submission(extraction_id):
    """Return the submission that already owns this extraction, if any.

    A submission that is ``Pending`` or ``Submitted`` still stands for the one
    order the extraction may become, so no second one may be created for it.
    """
    name = frappe.db.get_value(
        "HD External Order Submission",
        {"extraction": extraction_id, "status": ("in", ("Pending", "Submitted"))},
        "name",
    )
    return frappe.get_doc("HD External Order Submission", name).as_dict() if name else None


def _external_order_id(result):
    """Read the external order identifier out of whatever a connector returns."""
    if isinstance(result, dict):
        return result.get("external_order_id") or result.get("name") or result.get("id")
    return result


def _send_to_connector(submission):
    """Offer one recorded submission to the configured connector.

    Without a connector, or without an extraction to send, the submission is
    handed back untouched and stays ``Pending``. A connector that raises is a
    refused order like any other: the reason is recorded on the submission and
    it joins the queue instead of escaping to the caller.
    """
    connector = get_order_connector()
    if not connector or not submission.get("extraction"):
        return submission
    extraction = frappe.get_doc("HD Order Extraction", submission["extraction"])
    try:
        external_order_id = _external_order_id(connector(extraction.as_dict()))
    except Exception as exc:
        return apply_submission_result(submission_id=submission["name"], error=str(exc))
    return apply_submission_result(
        submission_id=submission["name"],
        external_order_id=external_order_id,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def submit_order(extraction_id, idempotency_key=None, automated=0):
    """Hand a completed order extraction to the configured external system.

    Without a connector the submission stays ``Pending``: Helpdesk records the
    intent and remains provider-neutral until an ERP integration is installed.
    A connector that raises is a refused order like any other: the reason is
    recorded on the submission instead of escaping to the caller.
    An extraction that is already on its way to the external system is returned
    as it stands, so the same order is never created twice.
    """
    extraction = frappe.get_doc("HD Order Extraction", extraction_id)
    if not extraction.ready_for_connector:
        frappe.throw(_("Order extraction {0} is not ready for the external system.").format(extraction_id))
    open_submission = _open_submission(extraction.name)
    if open_submission:
        return open_submission
    submission = record_submission(
        ticket_id=extraction.ticket,
        extraction=extraction.name,
        idempotency_key=idempotency_key,
        automated=automated,
    )
    return _send_to_connector(submission)


@frappe.whitelist()
@agent_only
def failed_submissions():
    """List the submissions the external system did not accept.

    These are the orders an agent has to finish by hand, either by retrying
    them or by creating the order in the external system directly.
    """
    return frappe.get_all(
        "HD External Order Submission",
        filters={"status": ("in", QUEUED_STATUSES)},
        fields=[
            "name",
            "ticket",
            "extraction",
            "status",
            "attempts",
            "last_error",
            "automated",
            "submitted_on",
        ],
        order_by="modified desc",
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def retry_submission(submission_id):
    """Offer a refused submission to the external system once more.

    Only an agent who may read the ticket may retry its order. The previous
    error is cleared once there really is something to attempt, so the queue
    shows only what is still outstanding; when nothing can be attempted the
    submission stays queued and says why. The attempt count is kept, so
    repeated failures still end up needing manual handling.
    """
    doc = frappe.get_doc("HD External Order Submission", submission_id)
    frappe.has_permission("HD Ticket", "read", doc=doc.ticket, throw=True)
    if doc.status not in QUEUED_STATUSES:
        frappe.throw(_("Submission {0} is not waiting to be retried.").format(submission_id))
    reason = None
    if not get_order_connector():
        reason = _("No external system is configured to retry this order.")
    elif not doc.extraction:
        reason = _("This submission has no order extraction left to send.")
    if reason:
        doc.last_error = reason
        doc.save(ignore_permissions=True)
        return doc.as_dict()
    doc.last_error = None
    doc.status = "Pending"
    doc.save(ignore_permissions=True)
    return _send_to_connector(doc.as_dict())
