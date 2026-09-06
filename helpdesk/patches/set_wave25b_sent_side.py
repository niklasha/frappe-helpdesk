"""Say, on every outbound translation row that predates the field, which side left.

Until Wave 25b an outbound row could only be read one way: an agent wrote the
original in the working language and the customer received the translation.
That is what every existing outbound row means, so they are all stamped
`Translation`.

An inbound row is left alone. It is the customer's own words and our reading
of them, and nothing of it ever left the house; stamping it would claim the
desk mailed the customer its reading of their mail.

The other value only appears from now on, on a reply an agent wrote themselves
in the customer's language: there the original is what was sent and the
translation is the house's archive copy.
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
        WHERE direction = 'Outbound'
          AND (sent_side IS NULL OR sent_side = '')
        """
    )
    frappe.db.commit()
