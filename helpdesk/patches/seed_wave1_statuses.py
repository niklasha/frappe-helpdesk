"""Seed the business statuses required by the first operational wave."""

import frappe


def execute():
    statuses = [
        ("New", "Open", "Red"),
        ("In Progress", "Open", "Blue"),
        ("Waiting for Customer", "Paused", "Orange"),
        ("Waiting Internal", "Paused", "Orange"),
        ("Ready to Create Order", "Open", "Purple"),
        ("Order Created", "Resolved", "Green"),
    ]
    for label_agent, category, color in statuses:
        if frappe.db.exists("HD Ticket Status", label_agent):
            continue
        frappe.get_doc(
            {
                "doctype": "HD Ticket Status",
                "label_agent": label_agent,
                "label_customer": label_agent,
                "category": category,
                "color": color,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)

