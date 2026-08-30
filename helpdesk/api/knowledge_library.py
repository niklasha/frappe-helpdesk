import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin


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


def _require_knowledge_admin():
    """Curating the library is an administrative act, not an agent action."""
    if not is_admin():
        frappe.throw(
            _("You are not permitted to administer the knowledge library."),
            frappe.PermissionError,
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_knowledge_article(title, content, category=None, article=None):
    """Create or update a knowledge-library article as an authorized user."""
    _require_knowledge_admin()
    doc = (
        frappe.get_doc("HD Article", article) if article else frappe.new_doc("HD Article")
    )
    doc.title = title
    doc.content = content
    if category:
        doc.category = category
    doc.save(ignore_permissions=True)
    return doc.as_dict()
