import frappe
from frappe import _
from frappe.model.document import Document

from helpdesk.helpdesk.doctype.hd_ticket_type.hd_ticket_type import (
    CLASSIFICATION_GROUPS,
    classification_group,
)


class HDTicketClassificationRule(Document):
    """An administrator-managed rule mapping ticket content to a ticket type."""

    def validate(self):
        self.adopt_the_catalogue_group()
        self.require_a_target()
        self.refuse_an_unknown_class()

    def adopt_the_catalogue_group(self):
        """Fill the coarse class from the type, so nobody is asked twice.

        Sixteen of the desk's twenty labels have no coarse class anyone would
        guess — that DEX and Paketsökning both roll up to Order is a decision
        the catalogue already carries. Asking an administrator to restate it per
        rule is how the two drift apart.
        """
        if self.ticket_type:
            self.classification = classification_group(self.ticket_type)

    def require_a_target(self):
        """A rule that names neither is inert, and reads as configured.

        That is worse than an error: the administrator believes the desk is
        sorting mail it is silently letting past.
        """
        if not self.ticket_type and not self.classification:
            frappe.throw(
                _("A rule must name a ticket type or a classification."),
                frappe.MandatoryError,
            )

    def refuse_an_unknown_class(self):
        """A coarse class outside the five costs the next matching message.

        `HD Ticket.classification_model` is a Select; writing a sixth word into
        it makes the ticket unsavable, and the ticket in question is an inbound
        email. A refusal here costs one rule at configuration time; the
        alternative costs a customer's message at delivery time.
        """
        if self.classification and self.classification not in CLASSIFICATION_GROUPS:
            frappe.throw(
                _("{0} is not one of the classifications a ticket can carry: {1}.").format(
                    self.classification, ", ".join(CLASSIFICATION_GROUPS)
                )
            )
