from frappe.model.document import Document


class HDAIPrompt(Document):
    """An instruction given to the AI, kept under administrative control."""

    def validate(self):
        self.require_prompt_administration()
        self.bump_version()

    def require_prompt_administration(self):
        """The role gate holds on every save path, not only the governed API."""
        if self.flags.ignore_permissions:
            return
        from helpdesk.api.governance import require_ai_prompt_admin

        require_ai_prompt_admin()

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
