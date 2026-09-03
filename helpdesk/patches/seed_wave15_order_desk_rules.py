"""Seed the keyword rules that file mail into the desk's own vocabulary.

The rules existed only on the demo, created by an operator script. That matters
more than it sounds, because the ordering between them is load-bearing and an
argument about ordering only holds if both rules exist: "Skapa order" must match
before the broad order rule, or every purchase order the desk receives is filed
as a plain order enquiry and loses the marking that says an order can be entered
without a proof.

Ordering, and why: the narrow labels run first (10-49), the broad fallbacks last
(50+). A new rule must not contain the bare word "order" — the matcher is
substring-based and inherited unchanged, so a rule saying "order" matches
"ordernummer", "beställningsorder" and every mail that merely mentions one. That
is what rule_order exists to contain, and it is the only lever an administrator
has over it.

A rule whose ticket type this site does not hold is skipped rather than raised.
A missing catalogue row costs one rule; an exception here stops `bench migrate`
for the whole bench, and the seeds run inside a try that swallows the reason.

Idempotent by label. A rule that already exists keeps its keywords and its
order — those are an administrator's to tune — but one that carries no ticket
type is completed rather than skipped. Skipping it would mean the sites that
most need this patch get nothing from it: the demo, and any site configured by
ops/seed_order_desk.py, already hold rules under these labels from before the
field existed, so a plain existence check hands them zero typed rules and leaves
the vocabulary split exactly where this wave found it.
"""

import frappe

# label, keywords, ticket type, order
ORDER_DESK_RULES = [
    ("Reklamation", "reklamation, reklamera, fel på, felaktig, complaint, defekt", "Reklamation", 10),
    ("DEX-produktion", "dex", "DEX", 11),
    ("Expressproduktion", "express", "Express", 12),
    ("DHL expresspaket", "dadom, dadot, dhl express", "DHL express", 13),
    ("Skapa order", "inköp, inkop, purchase order, skapa order", "Skapa order", 15),
    ("Korrektur godkänt", "korrektur godkänt, godkänt korrektur, korr godkänt", "Korrektur godkänt", 16),
    ("Omritning Indien", "omritning, rita om, vektorisera hos indien", "Omritning Indien", 17),
    ("Vectorizer", "vectorizer, vektorisera", "Vectorizer", 18),
    ("Leveransfråga", "leveranstid, när levereras, leveransdatum", "Leveransfråga", 20),
    ("Paketsökning", "spåra paket, hitta paket, var är paketet, kolli", "Paketsökning", 21),
    ("Offertändring", "ändra antal, justera offert, offertändring", "Offertändring", 22),
    ("Webinlogg", "webinlogg, inloggning till webshop, konto till webshopen", "Webinlogg", 25),
    ("Webshop", "webshop, webbshop, web shop", "Webshop", 26),
    ("Faktura", "faktura, fakturafråga, betalning", "Faktura", 30),
    ("Service maskin", "service, maskin, itek", "Service maskin", 31),
    # The broad fallbacks, last on purpose. "order" here would swallow every
    # narrow rule above it if it ran first.
    ("Order och inköp", "order, beställning, bestallning", "Skapa order", 50),
    ("Produktfråga", "produkt, product, material, tyg, passform", "Produktfråga", 51),
]


def execute():
    for label, keywords, ticket_type, order in ORDER_DESK_RULES:
        if not frappe.db.exists("HD Ticket Type", ticket_type):
            continue
        existing = frappe.db.get_value(
            "HD Ticket Classification Rule", {"label": label}, ["name", "ticket_type"], as_dict=True
        )
        if existing:
            if not existing.ticket_type:
                # Completing a row that predates the field, not overruling
                # anyone: saving through the document lets the controller fill
                # the coarse class from the catalogue as it would for a new one.
                doc = frappe.get_doc("HD Ticket Classification Rule", existing.name)
                doc.ticket_type = ticket_type
                doc.flags.ignore_permissions = True
                doc.save()
            continue
        frappe.get_doc(
            {
                "doctype": "HD Ticket Classification Rule",
                "label": label,
                "keywords": keywords,
                "ticket_type": ticket_type,
                "enabled": 1,
                "rule_order": order,
            }
        ).insert(ignore_permissions=True)
