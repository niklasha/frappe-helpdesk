"""The seven work queues of the agent sidebar.

A queue is a named filter over HD Ticket for the calling agent. The sidebar
shows a counter per queue and the ticket list shows the rows of one queue;
both are produced by the same criterion (``_criterion``) so the number and
the list cannot disagree.
"""

import frappe
from frappe import _
from frappe.query_builder import Criterion
from frappe.query_builder.functions import Count

from helpdesk.utils import agent_only

# Sidebar order. ``counts()`` answers with exactly these keys.
QUEUE_KEYS = (
    "all",
    "mine",
    "unassigned",
    "waiting_on_us",
    "waiting_on_customer",
    "sla_risk",
    "closed",
)

# An SLA is a risk while it is running and stays one once it has been missed;
# a breached ticket is the risk that already happened, not a resolved one.
SLA_RISK_STATES = ("First Response Due", "Resolution Due", "Failed")


def _criterion(queue: str, user: str, ticket):
    """The pypika criterion that defines ``queue`` for ``user``.

    ``ticket`` is the ``HD Ticket`` table the caller selects from. Raises on
    an unknown queue so a typo in the URL is not an empty list.
    """
    not_closed = ticket.status_category != "Resolved"
    is_open = ticket.status_category == "Open"
    # _assign is a JSON list of users; an empty one is null, "" or "[]".
    assigned_to_me = ticket._assign.like(f'%"{user}"%')
    unassigned = ticket._assign.isnull() | ticket._assign.isin(["", "[]"])

    if queue == "all":
        return not_closed
    if queue == "mine":
        return not_closed & assigned_to_me
    if queue == "unassigned":
        return not_closed & unassigned
    if queue == "waiting_on_us":
        # The customer spoke last (or the agent never has).
        return (
            is_open
            & ticket.last_customer_response.isnotnull()
            & (
                ticket.last_agent_response.isnull()
                | (ticket.last_customer_response > ticket.last_agent_response)
            )
        )
    if queue == "waiting_on_customer":
        # The agent spoke last (or the customer never has).
        return (
            is_open
            & ticket.last_agent_response.isnotnull()
            & (
                ticket.last_customer_response.isnull()
                | (ticket.last_agent_response >= ticket.last_customer_response)
            )
        )
    if queue == "sla_risk":
        return is_open & ticket.agreement_status.isin(list(SLA_RISK_STATES))
    if queue == "closed":
        return ticket.status_category == "Resolved"
    frappe.throw(_("Okänd kö: {0}").format(queue))


def _query(queue: str, user: str):
    ticket = frappe.qb.DocType("HD Ticket")
    return frappe.qb.from_(ticket).where(_criterion(queue, user, ticket)), ticket


@frappe.whitelist()
@agent_only
def tickets(queue: str) -> list[str]:
    """Names of every ticket in ``queue`` for the calling agent, newest first.

    Unpaged on purpose: the ticket list narrows itself to these names, and the
    sidebar's counter is ``len`` of this very list.
    """
    query, ticket = _query(queue, frappe.session.user)
    rows = (
        query.select(ticket.name)
        .orderby(ticket.modified, order=frappe.qb.desc)
        .run(pluck=True)
    )
    return list(rows)


@frappe.whitelist()
@agent_only
def counts() -> dict[str, int]:
    """One counter per queue for the calling agent, keyed as ``QUEUE_KEYS``."""
    user = frappe.session.user
    result = {}
    for key in QUEUE_KEYS:
        query, ticket = _query(key, user)
        result[key] = query.select(Count(ticket.name)).run()[0][0]
    return result
