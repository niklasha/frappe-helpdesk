"""Seed the languages this business corresponds in."""

import frappe


def execute():
    languages = [
        ("sv", "Svenska"),
        ("en", "English"),
        ("de", "Deutsch"),
        ("da", "Dansk"),
        ("no", "Norsk"),
        ("fi", "Suomi"),
    ]
    for language_code, language_name in languages:
        if frappe.db.exists("HD Supported Language", language_code):
            continue
        frappe.get_doc(
            {
                "doctype": "HD Supported Language",
                "language_code": language_code,
                "language_name": language_name,
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
