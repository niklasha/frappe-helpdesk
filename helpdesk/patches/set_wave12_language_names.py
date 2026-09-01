"""Put the seeded language names into Swedish.

The catalogue shipped with each language's own name for itself — English,
Deutsch, Dansk, Norsk, Suomi. That is right in a list of languages and wrong
where the name is actually used: the ticket view drops it into a sentence, and
for a fortnight the demo read "Ärendet kom på English och har översatts".

Only a name still holding the value the seed gave it is changed. Someone who has
renamed a language has said something this patch has no business overruling —
the same rule the prompt library and the OAuth scope backfills follow.
"""

import frappe

# The seeded name, and what it should have been.
RENAMED = {
    "en": ("English", "Engelska"),
    "de": ("Deutsch", "Tyska"),
    "da": ("Dansk", "Danska"),
    "no": ("Norsk", "Norska"),
    "fi": ("Suomi", "Finska"),
}


def execute():
    for language_code, (seeded, swedish) in RENAMED.items():
        name = frappe.db.get_value(
            "HD Supported Language", {"language_code": language_code}, "name"
        )
        if not name:
            continue
        if frappe.db.get_value("HD Supported Language", name, "language_name") != seeded:
            continue
        frappe.db.set_value("HD Supported Language", name, "language_name", swedish)
