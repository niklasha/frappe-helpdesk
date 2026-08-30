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


@frappe.whitelist()
@agent_only
def search_tickets_by_correction(reference):
    """Find tickets whose correction number matches the reference."""
    if not reference:
        return []
    return _newest_first(_link_matches(reference, "correction"))


def _extraction_matches(reference):
    """Return result rows for extracted orders naming the article."""
    extractions = frappe.get_all(
        "HD Order Extraction",
        filters={"product": reference},
        fields=["ticket"],
        order_by="creation desc",
    )
    rows = []
    for extraction in extractions:
        ticket = _ticket_row(extraction["ticket"])
        if not ticket:
            continue
        rows.append(
            {
                "ticket": ticket["name"],
                "subject": ticket["subject"],
                "link_type": "article",
                "target": reference,
                "_creation": ticket["creation"],
            }
        )
    return rows


def _without_duplicate_tickets(rows):
    """Keep the first row of every ticket, so a ticket is reported once."""
    seen = set()
    unique = []
    for row in rows:
        if row["ticket"] in seen:
            continue
        seen.add(row["ticket"])
        unique.append(row)
    return unique


@frappe.whitelist()
@agent_only
def search_tickets_by_article(reference):
    """Find tickets by article number, in links and in extracted orders."""
    if not reference:
        return []
    rows = _link_matches(reference, "article") + _extraction_matches(reference)
    return _newest_first(_without_duplicate_tickets(rows))


@frappe.whitelist()
@agent_only
def search_tickets_by_file(filename):
    """Find tickets by the name of a file attached to them."""
    if not filename:
        return []
    files = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "HD Ticket",
            "file_name": ["like", f"%{filename}%"],
        },
        fields=["attached_to_name", "file_name"],
        order_by="creation desc",
    )
    rows = []
    for entry in files:
        ticket = _ticket_row(entry["attached_to_name"])
        if not ticket:
            continue
        rows.append(
            {
                "ticket": ticket["name"],
                "subject": ticket["subject"],
                "file_name": entry["file_name"],
                "_creation": ticket["creation"],
            }
        )
    return _newest_first(_without_duplicate_tickets(rows))
