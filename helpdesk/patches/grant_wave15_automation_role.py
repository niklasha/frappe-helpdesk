"""Give the automation role to those who administered automations before it bit.

Wave 15 makes `Helpdesk Automation Manager` real: the four automation doctypes
stop granting every Agent write, and their controllers refuse a save without
the role. Until now the role existed and gated nothing, so nobody had reason to
hold it — a migrate that tightens the schema without handing the role to anyone
turns "restrict who may change automations" into "nobody may", which is a
lockout, not a limit.

Granted to every user who already holds Agent Manager: that is the role upstream
uses for its own automation-adjacent settings (SLA), and the nearest statement
of "this person administers the desk" the site carries. An administrator can
narrow it afterwards; a patch cannot know who the customer means.
"""

import frappe

from helpdesk.api.governance import AUTOMATION_MANAGER_ROLE


def execute():
    if not frappe.db.exists("Role", AUTOMATION_MANAGER_ROLE):
        return
    for user in frappe.get_all(
        "Has Role", filters={"role": "Agent Manager", "parenttype": "User"},
        fields=["parent"], distinct=True, limit_page_length=0,
    ):
        if frappe.db.exists("Has Role", {"parent": user.parent, "role": AUTOMATION_MANAGER_ROLE}):
            continue
        doc = frappe.get_doc("User", user.parent)
        doc.append("roles", {"role": AUTOMATION_MANAGER_ROLE})
        doc.flags.ignore_permissions = True
        doc.save()
