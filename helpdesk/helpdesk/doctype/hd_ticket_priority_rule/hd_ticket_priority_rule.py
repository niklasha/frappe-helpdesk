from frappe.model.document import Document


class HDTicketPriorityRule(Document):
    """An administrator-managed rule mapping ticket content to a priority."""

    def validate(self):
        self.require_automation_administration()

    def require_automation_administration(self):
        """Deciding how inbound mail is sorted, prioritised, routed or answered is
        an administrative act — AUTH-05, in the customer's words: "Det skall gå
        att begränsa vem som får ändra automationer."

        Upstream restricts its own automations out of the box (Assignment Rule
        is System Manager only). These doctypes were placed beside them with
        Agent write, so anyone on the desk could change the rules; the role that
        was meant to gate them has been seeded since Wave 5 and checked by no
        code path. The gate lives in the controller rather than only in the
        schema, as it does for HD AI Prompt, so it holds on every save path —
        seeds and operator scripts save with permissions ignored, and that is
        the one case that may pass.
        """
        if self.flags.ignore_permissions:
            return
        from helpdesk.api.governance import AUTOMATION_MANAGER_ROLE, require_role

        require_role(AUTOMATION_MANAGER_ROLE)

