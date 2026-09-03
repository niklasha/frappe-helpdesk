"""Give every existing ticket type a coarse class.

The field arrives with Wave 15 and every row that predates it reads blank. A
blank there is a hole in the rollup: a ticket typed with such a row would take
the fallback rather than an answer, and a report counting by
`classification_model` would show the hole as Övrigt without saying why.

Only a row whose group is still empty is touched. An administrator who has
already grouped a type has said something this patch has no business overruling
— the same rule the language-name backfill and the prompt library follow.

The field deliberately carries no `default`. A Select default is written into
every existing row the moment `bench migrate` adds the column, which would leave
this patch nothing to recognise as unanswered — the twenty labels would all read
Övrigt and the rollup would be silently wrong on precisely the sites that had
the vocabulary already. Empty means unanswered; `classification_group()` reads
empty as Övrigt at the point of use instead.
"""

import frappe

from helpdesk.patches.seed_wave15_order_desk_types import ORDER_DESK_TYPES

# The app's own types, which predate the desk vocabulary. Question and Bug are
# support conversations rather than order work; Incident and Unspecified have no
# claim on any of the four named classes.
APP_TYPES = {
    "Question": "Produktfråga",
    "Bug": "Övrigt",
    "Incident": "Övrigt",
    "Unspecified": "Övrigt",
}

FALLBACK_GROUP = "Övrigt"


def execute():
    known = {label: group for label, group, _p, _d in ORDER_DESK_TYPES}
    known.update(APP_TYPES)
    for row in frappe.get_all(
        "HD Ticket Type", fields=["name", "classification_group"], limit_page_length=0
    ):
        if row.classification_group:
            continue
        frappe.db.set_value(
            "HD Ticket Type",
            row.name,
            "classification_group",
            known.get(row.name, FALLBACK_GROUP),
        )
