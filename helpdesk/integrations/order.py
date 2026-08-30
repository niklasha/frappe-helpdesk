import frappe


def get_order_connector():
    """Return the configured external-order connector, if an app provides one.

    ERP-specific integrations register a dotted path through the
    ``order_connector`` Frappe hook; Helpdesk itself remains provider-neutral.
    """
    connectors = frappe.get_hooks("order_connector") or []
    if isinstance(connectors, str):
        connectors = [connectors]
    if len(connectors) > 1:
        frappe.throw("Only one order connector may be active")
    return frappe.get_attr(connectors[0]) if connectors else None
