"""Give every ticket that already has AI records its roll-up.

The hooks keep `HD Ticket.ai_cost` right from the moment they are installed;
the rows that existed before have never been summed. One recompute per
ticket that has any AI row, through the same function the hooks use, so the
backfill and the live path cannot disagree.
"""

import frappe

from helpdesk.api.ai_cost import AI_DOCTYPES, recompute


def execute():
    tickets = set()
    for doctype in AI_DOCTYPES:
        if not frappe.db.table_exists(doctype):
            continue
        for row in frappe.db.get_all(doctype, fields=["ticket"], distinct=True):
            if row.ticket:
                tickets.add(row.ticket)
    for ticket in tickets:
        recompute(ticket)
