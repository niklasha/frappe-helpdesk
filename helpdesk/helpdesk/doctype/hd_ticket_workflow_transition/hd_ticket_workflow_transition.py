from frappe.model.document import Document


class HDTicketWorkflowTransition(Document):
    """A configurable event-driven transition between ticket statuses."""

