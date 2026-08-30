# Copyright (c) 2021, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from helpdesk.utils import capture_event


class HDArticle(Document):
    def validate(self):
        self.validate_article_category()
        self.validate_published_content()
        self.version_edited_content()
        self.maintain_ai_approval()

    def version_edited_content(self):
        """Give every edit of an article its own version number.

        Versioning lives here rather than in the curation API so that an edit
        made through the agent knowledge base counts the same as a curated one.
        """
        before = None if self.is_new() else self.get_doc_before_save()
        edited = before and (
            before.title != self.title or before.content != self.content
        )
        if self.is_new() or edited or not cint(self.version):
            self.version = cint(self.version) + 1

    def maintain_ai_approval(self):
        """Approval belongs to the exact text that earned it.

        Editing an approved article withdraws its approval, and an approval
        always records the version it was granted for, whichever code path
        saved the article.
        """
        before = None if self.is_new() else self.get_doc_before_save()
        was_approved = bool(before and before.ai_approved)
        if was_approved and (
            before.title != self.title or before.content != self.content
        ):
            self.ai_approved = 0
        if self.ai_approved:
            self.ai_approved_version = cint(self.version)
        else:
            self.ai_approved_version = 0
            self.ai_approved_by = None
            self.ai_approved_on = None

    def on_update(self):
        self.snapshot_version()

    def snapshot_version(self):
        """Keep a copy of every version of the text that has ever existed."""
        if frappe.db.exists(
            "HD Article Revision", {"article": self.name, "version": self.version}
        ):
            return
        frappe.get_doc(
            {
                "doctype": "HD Article Revision",
                "article": self.name,
                "version": self.version,
                "title": self.title,
                "content": self.content,
                "revised_by": frappe.session.user,
                "revised_on": now_datetime(),
            }
        ).insert(ignore_permissions=True)

    def validate_article_category(self):
        if self.has_value_changed("category") and not self.is_new():
            old_category = self.get_doc_before_save().get("category")
            self.check_category_length(old_category)

    def validate_published_content(self):
        if self.status == "Published" and not self.content:
            frappe.throw(_("Published articles must have content."))

    def before_insert(self):
        self.author = frappe.session.user

    def before_save(self):
        # set published date of the hd_article
        if self.status == "Published" and not self.published_on:
            self.published_on = frappe.utils.now()
        elif self.status == "Draft" and self.published_on:
            self.published_on = None

        if self.status == "Archived" and self.category != None:
            self.category = None

        # index is only set if its not set already, this allows defining index
        # at the time of creation itself if not set the index is set to the
        # last index + 1, i.e. the hd_article is added at the end
        if self.status == "Published" and self.idx == -1:
            self.idx = cint(
                frappe.db.count(
                    "HD Article",
                    {"category": self.category, "status": "Published"},
                )
            )

    def after_insert(self):
        count = frappe.db.count("HD Article")
        if count == 1:
            return
        capture_event("article_created")

    def on_trash(self):
        self.check_category_length()
        self.discard_versions()

    def discard_versions(self):
        """A deleted article takes its revision history with it.

        This runs before Frappe's link check, so the revisions this doctype
        creates never stand in the way of deleting the article itself.
        """
        frappe.db.delete("HD Article Revision", {"article": self.name})

    def check_category_length(self, category=None):
        category = category or self.get("category")
        if not category:
            return
        category_articles = frappe.db.count("HD Article", {"category": category})
        if category_articles == 1:
            frappe.throw(_("Category must have atleast one article"))

    @staticmethod
    def default_list_data():
        columns = [
            {
                "label": "Title",
                "type": "Data",
                "key": "title",
                "width": "20rem",
            },
            {
                "label": "Status",
                "type": "status",
                "key": "status",
                "width": "10rem",
            },
            {
                "label": "Author",
                "type": "Link",
                "key": "author",
                "width": "17rem",
            },
            {
                "label": "Last Modified",
                "type": "Datetime",
                "key": "modified",
                "width": "8rem",
            },
        ]
        return {"columns": columns}

    @frappe.whitelist()
    def set_feedback(self, value: int):
        # 0 empty, 1 like, 2 dislike
        user = frappe.session.user

        feedback = frappe.db.exists(
            "HD Article Feedback", {"user": user, "article": self.name}
        )
        if feedback:
            current_value = frappe.db.get_value(
                "HD Article Feedback", feedback, "feedback"
            )
            if int(current_value) == value:
                return
            frappe.db.set_value("HD Article Feedback", feedback, "feedback", value)
            frappe.db.set_value("HD Article Feedback", feedback, "feedback", value)
        else:
            frappe.new_doc(
                "HD Article Feedback", user=user, article=self.name, feedback=value
            ).insert()

    @property
    def title_slug(self) -> str:
        """
        Generate slug from article title.
        Example: "Introduction to Frappe Helpdesk" -> "introduction-to-frappe-helpdesk"

        :return: Generated slug
        """
        return self.title.lower().replace(" ", "-")
