"""One number on the ticket: what its AI records have cost, in USD.

Every AI record (triage, translation, order extraction, reply draft) carries
its own `ai_cost` since Wave 17b, priced from the engine's tariff and the
tokens the runner reported. A coordinator asking "what did this ticket cost
us" should not have to open four lists and add them up, so HD Ticket carries
the sum.

It is a roll-up, not a counter: recomputed from the rows every time one of
them is inserted, changed or deleted, so deleting a record takes its cost
back out and a re-priced row is reflected. Rows without a cost (an adopted
translation, an unpriced engine, a runner that reported no usage) contribute
nothing rather than zero, and a ticket with no priced row keeps NULL — the
desk then shows no number instead of a misleading $0.
"""

import frappe

# The doctypes that carry `ai_cost` and a `ticket` link. Kept here so the
# hooks, the patch and the sum agree on one list.
AI_DOCTYPES = (
    "HD AI Triage Result",
    "HD Message Translation",
    "HD Order Extraction",
    "HD AI Reply Draft",
)


def total_for(ticket_id: str, exclude: tuple[str, str] | None = None) -> float | None:
    """Sum of `ai_cost` over the ticket's AI rows, None when no row has one.

    `exclude` names one (doctype, name) to leave out: the row being deleted,
    which is still in the table while its on_trash hook runs.
    """
    total = 0.0
    priced = False
    for doctype in AI_DOCTYPES:
        filters = {"ticket": ticket_id}
        if exclude and exclude[0] == doctype:
            filters["name"] = ["!=", exclude[1]]
        for row in frappe.db.get_all(doctype, filters=filters, fields=["ai_cost"]):
            cost = row.get("ai_cost")
            if cost in (None, ""):
                continue
            total += float(cost)
            priced = True
    return total if priced else None


def recompute(
    ticket_id: str, exclude: tuple[str, str] | None = None
) -> float | None:
    """Write the ticket's roll-up and return it.

    A direct column write: no save, no hooks, no `modified` bump. The ticket
    did not change in any sense an agent cares about, and going through the
    document would fire the ticket's own listeners and fight the SLA logic
    for a number that is bookkeeping.
    """
    if not ticket_id or not frappe.db.exists("HD Ticket", ticket_id):
        return None
    total = total_for(ticket_id, exclude)
    frappe.db.set_value(
        "HD Ticket", ticket_id, "ai_cost", total, update_modified=False
    )
    return total


def on_change(doc, method: str | None = None) -> None:
    """doc_events target for the AI doctypes: after_insert, on_update, on_trash.

    On trash the row is still in the table when the hook runs, so it is left
    out of the sum explicitly; a deleted record takes its cost back out.
    """
    ticket_id = getattr(doc, "ticket", None)
    if not ticket_id:
        return
    exclude = (doc.doctype, doc.name) if method == "on_trash" else None
    recompute(ticket_id, exclude)
