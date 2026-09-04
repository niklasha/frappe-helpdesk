import frappe
from frappe import _
from frappe.model.document import Document


class HDTicketType(Document):
    def on_trash(self):
        self.prevent_system_delete()

    def prevent_system_delete(self):
        if self.is_system:
            frappe.throw(_("System types can not be deleted"))


# The five words a ticket has carried since Wave 0. A sixth is a schema change.
CLASSIFICATION_GROUPS = ("Order", "Reklamation", "Produktfråga", "Webshop", "Övrigt")

FALLBACK_GROUP = "Övrigt"


@frappe.whitelist()
def match_ticket_type(named: str | None) -> str | None:
    """The enabled ticket type a written label names, or nothing.

    Exact after casefolding and collapsing whitespace, never a substring:
    "Order" must not quietly become "Skapa order", or the narrowest label the
    coordinator has wins over the one she meant. A model that writes a label the
    catalogue does not hold has named nothing, and recording nothing is the
    honest reading of it — the catalogue decides, whoever proposed.

    One indexed lookup rather than a scan. A `get_all` with no limit is not a
    catalogue but the first page of one: Frappe defaults page_length to 20, the
    desk has twenty labels plus the app's four, and the types past the first
    page would be invisible — a rule naming one would fall through to the
    default and a model answering correctly would resolve to nothing, both
    silently.
    """
    wanted = " ".join(str(named or "").split())
    if not wanted:
        return None
    return frappe.db.get_value(
        "HD Ticket Type", {"name": wanted, "disabled": 0}, "name"
    )


def classification_group(ticket_type: str | None) -> str:
    """Which of the five coarse classes a type rolls up to, or nothing.

    Nothing, deliberately, when the type states no group — the fallback belongs
    to the caller, not here. A type carrying no group has said nothing about
    what its tickets are, and answering Övrigt on its behalf is an answer: it
    silences the keyword rule and the subject line, which do have something to
    say. Every ticket type an administrator creates without choosing a group
    would otherwise file its whole catch as Övrigt, overruling a rule that says
    Reklamation.

    Found by a Wave 0 contract: a ticket titled "Order request" matched the
    subject ladder for Order, was typed with a group-less type left behind by an
    older test, and came out Övrigt.
    """
    if not ticket_type:
        return ""
    return frappe.get_cached_value(
        "HD Ticket Type", ticket_type, "classification_group"
    ) or ""
