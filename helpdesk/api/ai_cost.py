"""One number on the ticket: what its AI records have cost, in USD.

Every AI record (triage, translation, order extraction, reply draft) carries
its own `ai_cost` since Wave 17b, priced from the engine's tariff and the
tokens the runner reported, and a `cost_known` flag that says whether that
number is the whole price. A coordinator asking "what did this ticket cost
us" should not have to open four lists and add them up, so HD Ticket carries
the sum.

It is a roll-up, not a counter: recomputed from the rows every time one of
them is inserted, changed or deleted, so deleting a record takes its cost
back out and a re-priced row is reflected.

The rule (Wave 25b): the ticket's cost is the sum of what is known, and the
ticket says separately whether anything was unpriced. A row with `cost_known`
0 holds whatever could be priced — 0 for an adopted translation, an unpriced
engine or a runner that reported no usage, and the priced half of a draft
whose repair ran on an engine nobody has priced — so it is summed like any
other, and `ai_cost_unpriced` is set so nobody reads the total as complete.
Leaving such rows out, the old rule, made real money vanish from the total
the moment part of a row was unknown.
"""

import frappe
from frappe.utils import cint, flt

# The doctypes that carry `ai_cost`, `cost_known` and a `ticket` link. Kept
# here so the hooks, the patch and the sum agree on one list.
AI_DOCTYPES = (
    "HD AI Triage Result",
    "HD Message Translation",
    "HD Order Extraction",
    "HD AI Reply Draft",
)

UNPRICED_FIELD = "ai_cost_unpriced"


def costs_for(ticket_id: str) -> tuple[float, bool]:
    """The sum of `ai_cost` over the ticket's AI rows, and whether any was unpriced."""
    total = 0.0
    unpriced = False
    for doctype in AI_DOCTYPES:
        # Summed here rather than in SQL: this bench's Frappe refuses function
        # strings in SELECT ("SQL functions are not allowed as strings"), and
        # a ticket has a handful of AI rows, not thousands.
        rows = frappe.db.get_all(
            doctype,
            filters={"ticket": ticket_id},
            fields=["ai_cost", "cost_known"],
        )
        total += sum(flt(row.get("ai_cost")) for row in rows)
        unpriced = unpriced or any(not cint(row.get("cost_known")) for row in rows)
    return total, unpriced


def total_for(ticket_id: str) -> float:
    """Sum of `ai_cost` over the ticket's AI rows."""
    return costs_for(ticket_id)[0]


def recompute(ticket_id: str) -> float | None:
    """Write the ticket's roll-up and return it.

    A direct column write: no save, no hooks, no `modified` bump. The ticket
    did not change in any sense an agent cares about, and going through the
    document would fire the ticket's own listeners and fight the SLA logic
    for a number that is bookkeeping.
    """
    if not ticket_id or not frappe.db.exists("HD Ticket", ticket_id):
        return None
    total, unpriced = costs_for(ticket_id)
    values = {"ai_cost": total}
    if frappe.get_meta("HD Ticket").has_field(UNPRICED_FIELD):
        values[UNPRICED_FIELD] = 1 if unpriced else 0
    frappe.db.set_value("HD Ticket", ticket_id, values, update_modified=False)
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
