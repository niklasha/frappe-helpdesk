import json

import frappe

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
