import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

IMMUTABLE_FIELDS = (
    "actor",
    "action",
    "reference_doctype",
    "reference_name",
    "details",
    "occurred_on",
    "performed_by",
)


class HDAutomationEvent(Document):
    """One recorded action taken by automation, AI or a person."""

    def validate(self):
        self.protect_recorded_history()
        self.stamp_attribution()

    def protect_recorded_history(self):
        """What automation did is history, and history is never rewritten."""
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if not before:
            return
        for field in IMMUTABLE_FIELDS:
            if (before.get(field) or None) != (self.get(field) or None):
                frappe.throw(_("A recorded automation event cannot be changed."))

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
