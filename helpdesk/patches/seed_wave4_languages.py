"""Seed the languages this business corresponds in."""

import frappe


def execute():
    # Swedish names, not each language's own. The catalogue is read by
    # Swedish-speaking agents, and the name is dropped straight into a sentence:
    # "Ärendet kom på Engelska och har översatts". The endonyms read as a typo
    # there — the ticket view said "Ärendet kom på English" for a fortnight.
    languages = [
        ("sv", "Svenska"),
        ("en", "Engelska"),
        ("de", "Tyska"),
        ("da", "Danska"),
        ("no", "Norska"),
        ("fi", "Finska"),
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
