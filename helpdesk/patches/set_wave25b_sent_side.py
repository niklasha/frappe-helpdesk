"""Say, on every translation row that predates the field, which side left the house.

Until Wave 25b a row could only be read one way: an agent wrote the original in
the working language and the customer received the translation. That is what
every existing row means, so they are all stamped `Translation` — including the
inbound ones, where the customer's own words are the original and our reading of
them never went anywhere.

The other value only appears from now on, on a reply an agent wrote themselves in
the customer's language: there the original is what was sent and the translation
is the house's archive copy.
"""

import frappe


def execute():
    if not frappe.db.table_exists("HD Message Translation"):
        return
    frappe.reload_doc("helpdesk", "doctype", "hd_message_translation")
    # One statement rather than a document per row: this is a backfill of a
    # read-only record field, and saving each row would fire hooks and rewrite
    # `modified` on history nobody touched.
    frappe.db.sql(
        """
        UPDATE `tabHD Message Translation`
        SET sent_side = 'Translation'
        WHERE sent_side IS NULL OR sent_side = ''
        """
    )
