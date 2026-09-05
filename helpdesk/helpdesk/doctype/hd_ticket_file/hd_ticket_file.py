from frappe.model.document import Document


class HDTicketFile(Document):
    """One File on a ticket, classified from its bytes.

    Written only by `helpdesk.api.ticket_files`: the verdict (kind, vector,
    format) is deterministic and never an opinion. The model's opinion about
    the file lives in `assessment`, marked `assessed_by = model`, so the desk
    can tell the two apart.
    """
