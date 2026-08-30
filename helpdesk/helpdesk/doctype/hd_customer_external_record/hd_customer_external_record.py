import frappe
from frappe import _
from frappe.model.document import Document

RECORD_TYPES = ("Order", "Proof")


class HDCustomerExternalRecord(Document):
    """One order or proof a customer has in an external system."""

    def validate(self):
        """Refuse a record type the customer history does not know about."""
        if self.record_type not in RECORD_TYPES:
            frappe.throw(
                _("{0} is not a customer history record type.").format(
                    self.record_type or _("Empty")
                )
            )
