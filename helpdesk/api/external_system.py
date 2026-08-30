import frappe

from helpdesk.utils import agent_only


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
