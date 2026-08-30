import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class HDAutomationEvent(Document):
    """One recorded action taken by automation, AI or a person."""

    def validate(self):
        self.stamp_attribution()

    def stamp_attribution(self):
        """An automated or AI change is never recorded as a human action.

        Attribution is the server's to assign: who acted and when are taken
        from the session as the event is recorded, never from the caller. The
        `actor` the caller declared is left exactly as it was given.
        """
        if not self.actor:
            self.actor = "Automation"
        if not self.is_new():
            return
        self.performed_by = frappe.session.user
        self.occurred_on = now_datetime()
