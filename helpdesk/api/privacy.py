import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

from helpdesk.utils import agent_only, is_admin


def require_privacy_admin():
    """Privacy administration is reserved for administrators."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator may administer privacy settings."),
            frappe.PermissionError,
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def set_retention_policy(reference_doctype, retain_days):
    """Configure for how many days documents of one doctype are kept."""
    require_privacy_admin()
    existing = frappe.db.exists("HD Retention Policy", reference_doctype)
    doc = (
        frappe.get_doc("HD Retention Policy", existing)
        if existing
        else frappe.get_doc(
            {"doctype": "HD Retention Policy", "reference_doctype": reference_doctype}
        )
    )
    doc.retain_days = cint(retain_days)
    doc.enabled = 1
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def retention_policies():
    """Return the retention periods currently in force."""
    return frappe.get_all(
        "HD Retention Policy",
        filters={"enabled": 1},
        fields=["name", "reference_doctype", "retain_days", "enabled"],
        order_by="reference_doctype asc",
    )


@frappe.whitelist()
@agent_only
def expired_documents(reference_doctype):
    """Report the documents that have outlived their retention period.

    Reporting only: Helpdesk names what has expired and deletes nothing, so
    removal stays a deliberate act.
    """
    require_privacy_admin()
    policy = frappe.db.get_value(
        "HD Retention Policy",
        {"reference_doctype": reference_doctype, "enabled": 1},
        ["retain_days"],
        as_dict=True,
    )
    if not policy:
        return []
    cutoff = add_days(now_datetime(), -cint(policy.retain_days))
    return frappe.get_all(
        reference_doctype,
        filters={"creation": ["<", cutoff]},
        pluck="name",
        order_by="creation asc",
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def anonymize_contact(email):
    """Replace a contact's address on their tickets with an irreversible placeholder.

    The placeholder carries a random hash, so the original address cannot be
    derived from it and the erasure cannot be undone.
    """
    require_privacy_admin()
    if not email:
        return 0
    tickets = frappe.get_all("HD Ticket", filters={"raised_by": email}, pluck="name")
    if not tickets:
        return 0
    placeholder = f"anonymized-{frappe.generate_hash(length=12)}@example.invalid"
    for ticket in tickets:
        frappe.db.set_value("HD Ticket", ticket, "raised_by", placeholder)
    frappe.logger("helpdesk").info(
        f"Anonymized {len(tickets)} tickets as {placeholder} by {frappe.session.user}"
    )
    return len(tickets)
