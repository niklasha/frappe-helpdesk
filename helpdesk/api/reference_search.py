import frappe

from helpdesk.utils import agent_only


def _ticket_row(ticket_id):
    """Return the searchable core of a ticket, or None when it is gone."""
    if not ticket_id:
        return None
    return frappe.db.get_value(
        "HD Ticket", ticket_id, ["name", "subject", "creation"], as_dict=True
    )


def _link_matches(reference, kind=None):
    """Return result rows for external links pointing at the reference."""
    filters = {"target": reference}
    if kind:
        filters["link_type"] = kind
    links = frappe.get_all(
        "HD Ticket External Link",
        filters=filters,
        fields=["ticket", "link_type", "target"],
        order_by="creation desc",
    )
    rows = []
    for link in links:
        ticket = _ticket_row(link["ticket"])
        if not ticket:
            continue
        rows.append(
            {
                "ticket": ticket["name"],
                "subject": ticket["subject"],
                "link_type": link["link_type"],
                "target": link["target"],
                "_creation": ticket["creation"],
            }
        )
    return rows


def _newest_first(rows):
    """Order result rows by their ticket, newest first."""
    ordered = sorted(rows, key=lambda row: row["_creation"], reverse=True)
    for row in ordered:
        row.pop("_creation", None)
    return ordered


@frappe.whitelist()
@agent_only
def search_tickets_by_reference(reference, kind=None):
    """Find tickets whose external references match, e.g. an order number."""
    if not reference:
        return []
    return _newest_first(_link_matches(reference, kind))
