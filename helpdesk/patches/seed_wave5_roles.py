"""Seed the roles that gate automation, AI and knowledge administration."""

import frappe

from helpdesk.api.governance import HELPDESK_ROLES


def execute():
    for role_name in HELPDESK_ROLES:
        if frappe.db.exists("Role", role_name):
            continue
        frappe.get_doc(
            {
                "doctype": "Role",
                "role_name": role_name,
                "desk_access": 0,
                "home_page": "/helpdesk",
            }
        ).insert(ignore_permissions=True)
