"""One number on the ticket: what its AI records have cost, in USD.

Every AI record (triage, translation, order extraction, reply draft) carries
its own `ai_cost` since Wave 17b, priced from the engine's tariff and the
tokens the runner reported, and a `cost_known` flag that says whether that
number is one the desk can vouch for. A coordinator asking "what did this
ticket cost us" should not have to open four lists and add them up, so HD
Ticket carries the sum.

It is a roll-up, not a counter: recomputed from the rows every time one of
them is inserted, changed or deleted, so deleting a record takes its cost
back out and a re-priced row is reflected. Only rows with `cost_known` set
count: an adopted translation, an unpriced engine or a runner that reported
no usage holds a 0 the database forced on it, not a price, and is left out.
A ticket with no priced row sums to 0.
"""

import frappe
from frappe.utils import flt

# The doctypes that carry `ai_cost`, `cost_known` and a `ticket` link. Kept
# here so the hooks, the patch and the sum agree on one list.
AI_DOCTYPES = (
    "HD AI Triage Result",
    "HD Message Translation",
    "HD Order Extraction",
    "HD AI Reply Draft",
)


def total_for(ticket_id: str) -> float:
    """Sum of `ai_cost` over the ticket's AI rows whose cost is known."""
    total = 0.0
    for doctype in AI_DOCTYPES:
        # Summed here rather than in SQL: this bench's Frappe refuses function
        # strings in SELECT ("SQL functions are not allowed as strings"), and
        # a ticket has a handful of AI rows, not thousands.
        rows = frappe.db.get_all(
            doctype,
            filters={"ticket": ticket_id, "cost_known": 1},
            fields=["ai_cost"],
        )
        total += sum(flt(row.get("ai_cost")) for row in rows)
    return total


def recompute(ticket_id: str) -> float | None:
    """Write the ticket's roll-up and return it.

    A direct column write: no save, no hooks, no `modified` bump. The ticket
    did not change in any sense an agent cares about, and going through the
    document would fire the ticket's own listeners and fight the SLA logic
    for a number that is bookkeeping.
    """
    if not ticket_id or not frappe.db.exists("HD Ticket", ticket_id):
        return None
    total = total_for(ticket_id)
    frappe.db.set_value(
        "HD Ticket", ticket_id, "ai_cost", total, update_modified=False
    )
    return total


def on_change(doc, method: str | None = None) -> None:
    """doc_events target for the AI doctypes: after_insert, on_update, after_delete.

    Every event recomputes from the table as it stands. The delete side is
    hooked after the row is gone (after_delete, not on_trash) so the same
    plain sum serves all three and nothing has to be excluded by hand.
    """
    ticket_id = getattr(doc, "ticket", None)
    if not ticket_id:
        return
    recompute(ticket_id)
