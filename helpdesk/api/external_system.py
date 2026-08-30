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
