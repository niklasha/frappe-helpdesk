import frappe
from frappe.utils import cint

from helpdesk.utils import agent_only


@frappe.whitelist()
@agent_only
def search_knowledge(query, limit=5):
    """Return knowledge-library articles matching the query.

    The AI reply layer retrieves candidate source material through this
    contract; Helpdesk stays the authority over the library itself.
    """
    if not query:
        return []
    like = f"%{query}%"
    return frappe.get_all(
        "HD Article",
        or_filters={"title": ["like", like], "content": ["like", like]},
        fields=["name", "title", "content", "category"],
        order_by="modified desc",
        limit_page_length=cint(limit) or 5,
    )
