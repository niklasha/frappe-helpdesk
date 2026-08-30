import frappe
from frappe import _
from frappe.utils import cint, now_datetime

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
