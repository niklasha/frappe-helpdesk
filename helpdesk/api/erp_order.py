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
