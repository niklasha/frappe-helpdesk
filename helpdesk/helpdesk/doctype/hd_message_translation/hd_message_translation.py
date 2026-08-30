import frappe
from frappe import _
from frappe.model.document import Document


class HDMessageTranslation(Document):
    """A translated message stored alongside the text it came from."""

    def validate(self):
        self.protect_original_text()

    def protect_original_text(self):
        """The customer's own words must survive every later correction."""
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before and before.original_text != self.original_text:
            frappe.throw(_("The original text of a translation cannot be changed."))
