import frappe
from frappe import _
from frappe.model.document import Document

SUPPORTED_KINDS = (
    "openai",
    "openai_compatible",
    "openai_responses",
    "responses",
    "anthropic",
)


class HDAIEngine(Document):
    """One raphain provider Helpdesk knows how to describe.

    Helpdesk is the configuration authority for the engines a
    raphain-embedding runner talks to; it never performs inference itself.
    """

    def validate(self):
        """An engine raphain cannot instantiate is not a usable engine."""
        self.validate_kind()

    def validate_kind(self):
        """raphain has a closed set of adapters; anything else is a typo."""
        if self.kind and self.kind not in SUPPORTED_KINDS:
            frappe.throw(
                _("{0} is not a supported engine kind. Use one of: {1}.").format(
                    self.kind, ", ".join(SUPPORTED_KINDS)
                )
            )
