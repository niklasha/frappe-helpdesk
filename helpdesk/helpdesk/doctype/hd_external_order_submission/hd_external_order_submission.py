import frappe
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class HDExternalOrderSubmission(Document):
    """One attempt to create an order in an external system."""

    def validate(self):
        """Record whether automation or a person caused this submission.

        The audit belongs to the record itself, so an order created without a
        human in the loop is never attributed to whoever happened to be logged
        in, whichever code path saves the submission.
        """
        self.audit_actor = "Automation" if cint(self.automated) else frappe.session.user
        self.audit_timestamp = now_datetime()
