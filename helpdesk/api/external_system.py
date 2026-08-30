import json

import frappe
from frappe.utils import now_datetime

from helpdesk.utils import agent_only

SYNCABLE_CUSTOMER_FIELDS = (
    "customer_name",
    "customer_type",
    "domain",
    "erpnext_customer",
    "country",
    "image",
)


@frappe.whitelist()
@agent_only
def list_external_systems():
    """Return the external systems Helpdesk is configured to reach."""
    return frappe.get_all(
        "HD External System",
        filters={"enabled": 1},
        fields=["name", "system", "label", "link_template", "enabled"],
        order_by="system asc",
    )


def _enabled_system(system):
    """Return the enabled external system with that name, or None."""
    if not system:
        return None
    return frappe.db.get_value(
        "HD External System",
        {"system": system, "enabled": 1},
        ["name", "system", "label", "link_template"],
        as_dict=True,
    )


@frappe.whitelist()
@agent_only
def external_link_url(system, identifier):
    """Resolve a deep link into an external system from its link template."""
    row = _enabled_system(system)
    if not row or not row.link_template:
        return None
    return row.link_template.replace("{id}", str(identifier or ""))


@frappe.whitelist()
@agent_only
def ticket_external_links(ticket_id):
    """Return a ticket's external links, each with its resolved URL."""
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    links = frappe.get_all(
        "HD Ticket External Link",
        filters={"ticket": ticket_id},
        fields=["name", "ticket", "link_type", "target", "label"],
        order_by="creation asc",
    )
    for link in links:
        link["url"] = external_link_url(link["link_type"], link["target"])
    return links


@frappe.whitelist(methods=["POST"])
@agent_only
def sync_customer(customer, external_id=None, values=None):
    """Store the external system's view of a customer on its Helpdesk record."""
    frappe.has_permission("HD Customer", "write", doc=customer, throw=True)
    doc = frappe.get_doc("HD Customer", customer)
    if external_id:
        doc.erpnext_customer = external_id
    if isinstance(values, str):
        values = json.loads(values)
    for field, value in (values or {}).items():
        if field in SYNCABLE_CUSTOMER_FIELDS:
            setattr(doc, field, value)
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def record_customer_history(
    customer,
    record_type,
    external_id,
    summary=None,
    occurred_on=None,
    idempotency_key=None,
):
    """Record an order or proof a customer already has in an external system."""
    if idempotency_key:
        name = frappe.db.get_value(
            "HD Customer External Record", {"idempotency_key": idempotency_key}, "name"
        )
        if name:
            return frappe.get_doc("HD Customer External Record", name).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD Customer External Record",
            "customer": customer,
            "record_type": record_type,
            "external_id": external_id,
            "summary": summary,
            "occurred_on": occurred_on or now_datetime(),
            "idempotency_key": idempotency_key,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def customer_history(customer, record_type=None):
    """Return what a customer has ordered or approved before, newest first."""
    filters = {"customer": customer}
    if record_type:
        filters["record_type"] = record_type
    return frappe.get_all(
        "HD Customer External Record",
        filters=filters,
        fields=[
            "name",
            "customer",
            "record_type",
            "external_id",
            "summary",
            "occurred_on",
        ],
        order_by="occurred_on desc, creation desc",
    )


@frappe.whitelist()
@agent_only
def customer_proofs(customer):
    """Return the proofs a customer has seen before, newest first."""
    return customer_history(customer, record_type="Proof")
