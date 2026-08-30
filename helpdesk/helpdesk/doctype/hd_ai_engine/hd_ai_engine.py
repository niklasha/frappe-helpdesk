from frappe.model.document import Document


class HDAIEngine(Document):
    """One raphain provider Helpdesk knows how to describe.

    Helpdesk is the configuration authority for the engines a
    raphain-embedding runner talks to; it never performs inference itself.
    """
