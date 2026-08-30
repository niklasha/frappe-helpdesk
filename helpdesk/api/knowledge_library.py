import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from helpdesk.api.governance import KNOWLEDGE_MANAGER_ROLE, has_helpdesk_role
from helpdesk.utils import agent_only, is_admin


@frappe.whitelist()
@agent_only
def search_knowledge(
    query: str, limit: int | None = 5, category: str | None = None
) -> list:
    """Return knowledge-library articles matching the query.

    The AI reply layer retrieves candidate source material through this
    contract; Helpdesk stays the authority over the library itself.
    """
    if not query:
        return []
    like = f"%{query}%"
    filters = {"ai_approved": 1}
    if category:
        filters["category"] = category
    return frappe.get_all(
        "HD Article",
        filters=filters,
        or_filters={"title": ["like", like], "content": ["like", like]},
        fields=["name", "title", "content", "category", "version", "ai_approved_version"],
        order_by="modified desc",
        limit_page_length=cint(limit) or 5,
    )


def _require_knowledge_admin():
    """Curating the library is an administrative act, not an agent action."""
    if is_admin() or has_helpdesk_role(KNOWLEDGE_MANAGER_ROLE):
        return
    frappe.throw(
        _("You are not permitted to administer the knowledge library."),
        frappe.PermissionError,
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_knowledge_article(
    title: str,
    content: str,
    category: str | None = None,
    article: str | None = None,
) -> dict:
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


@frappe.whitelist(methods=["POST"])
@agent_only
def approve_knowledge_article(article: str) -> dict:
    """Approve the current version of an article for use in AI replies."""
    _require_knowledge_admin()
    doc = frappe.get_doc("HD Article", article)
    doc.ai_approved = 1
    doc.ai_approved_by = frappe.session.user
    doc.ai_approved_on = now_datetime()
    doc.save(ignore_permissions=True)
    return doc.as_dict()
