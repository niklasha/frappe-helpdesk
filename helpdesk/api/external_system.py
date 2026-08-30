import json

import frappe
from frappe import _
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
def list_external_systems() -> list:
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
def external_link_url(system: str | None, identifier: str | None) -> str | None:
    """Resolve a deep link into an external system from its link template."""
    row = _enabled_system(system)
    if not row or not row.link_template:
        return None
    return row.link_template.replace("{id}", str(identifier or ""))


@frappe.whitelist()
@agent_only
def ticket_external_links(ticket_id: str) -> list:
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
def sync_customer(
    customer: str,
    external_id: str | None = None,
    values: dict | list | str | None = None,
) -> dict:
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
    customer: str,
    record_type: str,
    external_id: str,
    summary: str | None = None,
    occurred_on: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
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
def customer_history(customer: str, record_type: str | None = None) -> list:
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
def customer_proofs(customer: str) -> list:
    """Return the proofs a customer has seen before, newest first."""
    return customer_history(customer, record_type="Proof")


@frappe.whitelist(methods=["POST"])
@agent_only
def sync_account_manager(customer: str, account_manager: str) -> dict:
    """Take the customer's account owner from the external system."""
    frappe.has_permission("HD Customer", "write", doc=customer, throw=True)
    if not frappe.db.exists("User", account_manager):
        frappe.throw(_("User {0} does not exist.").format(account_manager))
    doc = frappe.get_doc("HD Customer", customer)
    doc.account_manager = account_manager
    doc.save(ignore_permissions=True)
    return {
        "name": doc.name,
        "account_manager": doc.account_manager,
        "key_account_agent": doc.key_account_agent,
    }
