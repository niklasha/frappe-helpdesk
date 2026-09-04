"""The seven work queues of the agent sidebar.

A queue is a named filter over HD Ticket for the calling agent. The sidebar
shows a counter per queue and the ticket list shows the rows of one queue;
both are produced by the same criterion (``_criterion``) so the number and
the list cannot disagree.
"""

import frappe
from frappe import _

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


def _names(queue: str, user: str) -> list[str]:
    """Names of every ticket in ``queue`` that ``user`` may see, newest first.

    The criterion is evaluated with the query builder (two of the queues
    compare columns with each other, which frappe filters cannot express), and
    the result is then narrowed with ``frappe.get_list`` so the HD Ticket
    permission query (``hd_ticket.permission_query``; restricts by agent group
    when HD Settings says so) applies exactly as it does to the ticket list.
    """
    ticket = frappe.qb.DocType("HD Ticket")
    candidates = (
        frappe.qb.from_(ticket)
        .select(ticket.name)
        .where(_criterion(queue, user, ticket))
        .run(pluck=True)
    )
    if not candidates:
        return []
    return frappe.get_list(
        "HD Ticket",
        filters={"name": ["in", list(candidates)]},
        order_by="modified desc",
        pluck="name",
        ignore_permissions=False,
    )


@frappe.whitelist()
@agent_only
def tickets(queue: str) -> list[str]:
    """Names of every ticket in ``queue`` for the calling agent, newest first.

    Unpaged on purpose: the ticket list narrows itself to these names, and the
    sidebar's counter is ``len`` of this very list.
    """
    return _names(queue, frappe.session.user)


@frappe.whitelist()
@agent_only
def counts() -> dict[str, int]:
    """One counter per queue for the calling agent, keyed as ``QUEUE_KEYS``.

    Each counter is the length of what ``tickets`` answers for that queue, so
    the number and the list cannot disagree.
    """
    user = frappe.session.user
    return {key: len(_names(key, user)) for key in QUEUE_KEYS}
