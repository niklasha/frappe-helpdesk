from frappe.model.document import Document


class HDAIPrompt(Document):
    """An instruction given to the AI, kept under administrative control."""

    def validate(self):
        self.bump_version()

    def bump_version(self):
        """Every change to a prompt's content becomes a new version of it."""
        if self.is_new():
            self.version = 1
            return
        before = self.get_doc_before_save()
        if not before:
            return
        if before.prompt != self.prompt or before.purpose != self.purpose:
            self.version = (before.version or 0) + 1
