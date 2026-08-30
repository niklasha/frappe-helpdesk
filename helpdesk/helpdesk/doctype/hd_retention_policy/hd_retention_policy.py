import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class HDRetentionPolicy(Document):
    """How long documents of one doctype are kept before they expire."""

    def validate(self):
        """A retention period is meaningless unless it spans at least a day."""
        if cint(self.retain_days) < 1:
            frappe.throw(_("Retention must be at least one day."))
        if not frappe.db.exists("DocType", self.reference_doctype):
            frappe.throw(_("Unknown doctype {0}.").format(self.reference_doctype))
