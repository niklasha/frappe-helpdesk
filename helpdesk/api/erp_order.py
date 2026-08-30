import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from helpdesk.integrations.order import get_order_connector
from helpdesk.utils import agent_only


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
    a refusal is kept with its error so the attempt can be repeated.
    """
    doc = frappe.get_doc("HD External Order Submission", submission_id)
    if error:
        doc.attempts = cint(doc.attempts) + 1
        doc.last_error = error
        doc.status = "Failed"
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


def _external_order_id(result):
    """Read the external order identifier out of whatever a connector returns."""
    if isinstance(result, dict):
        return result.get("external_order_id") or result.get("name") or result.get("id")
    return result


@frappe.whitelist(methods=["POST"])
@agent_only
def submit_order(extraction_id, idempotency_key=None, automated=0):
    """Hand a completed order extraction to the configured external system.

    Without a connector the submission stays ``Pending``: Helpdesk records the
    intent and remains provider-neutral until an ERP integration is installed.
    A connector that raises is a refused order like any other: the reason is
    recorded on the submission instead of escaping to the caller.
    """
    extraction = frappe.get_doc("HD Order Extraction", extraction_id)
    if not extraction.ready_for_connector:
        frappe.throw(_("Order extraction {0} is not ready for the external system.").format(extraction_id))
    submission = record_submission(
        ticket_id=extraction.ticket,
        extraction=extraction.name,
        idempotency_key=idempotency_key,
        automated=automated,
    )
    connector = get_order_connector()
    if not connector:
        return submission
    try:
        external_order_id = _external_order_id(connector(extraction.as_dict()))
    except Exception as exc:
        return apply_submission_result(submission_id=submission["name"], error=str(exc))
    return apply_submission_result(
        submission_id=submission["name"],
        external_order_id=external_order_id,
    )
