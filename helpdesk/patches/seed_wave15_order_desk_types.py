"""Seed the order desk's own vocabulary as ticket types.

The twenty labels the order coordinator marks mail with existed only because an
operator script created them on the demo by hand (`ops/seed_order_desk.py`,
3 September 2026). Everything Wave 15 builds points at them: a rule Links to
one, an AI proposal is resolved against one, an acceptance writes one. A patch
that Links to a row the app never creates raises LinkValidationError and stops
`bench migrate` for the whole bench — on every site that has not seen the
operator script, which is every acceptance run and every new install.

Each row carries the coarse class it rolls up to. The rule for that mapping:
the rollup answers "what is this message about" in the five words the reports
already count by. Prepress and shipping steps sit inside an order, so they roll
up to Order; a marking about how mail is *handled* (Hör ihop) and a hand-off to
another department (Faktura, Service maskin) do not.

Idempotent by name, and a row that already exists is never touched — an
administrator's edits to description, priority or grouping outrank this seed.
"""

import frappe

# label, coarse class, default priority, description
ORDER_DESK_TYPES = [
    ("DEX", "Order", "Urgent", "DEX-produktion, vår snabbaste. Prioriteras och gulmarkeras."),
    ("Express", "Order", "High", "Express-produktion. Prioriteras och gulmarkeras."),
    ("Reklamation", "Reklamation", "High", "Reklamation. Prioriteras; tas i första hand av den som äger reklamationer."),
    ("Skapa order", "Order", "Medium", "Färdigt inköp eller kund vi lägger order på direkt, utan korrektur."),
    ("Korrektur godkänt", "Order", "Medium", "Godkännande på korrektur; den som gjorde korret skapar ordern."),
    ("Omritning Indien", "Order", "Medium", "Fil som skickas till Indien för omritning till vektor."),
    ("Vectorizer", "Order", "Medium", "Fil som inte är vektor men som vi kan vektorisera själva."),
    ("Original hos annan kund", "Order", "Medium", "Loggan finns i vektor på en annan kund; nya artiklar kan skapas på den."),
    ("DHL express", "Order", "Medium", "Order som ska skickas med DHL expresspaket (DADOM/DADOT)."),
    ("Hör ihop", "Övrigt", "Low", "Flera mail från samma kund i samma ärende som ska paras ihop."),
    ("Webinlogg", "Webshop", "Low", "Skapa webinlogg till kund."),
    ("Inväntar frakt", "Order", "Low", "Fråga skickad till frakt, väntar på svar från fraktavdelningen."),
    ("Leveransfråga", "Order", "Medium", "Fråga om leveranstid."),
    ("Paketsökning", "Order", "Medium", "Sök efter paket."),
    ("Offertändring", "Order", "Medium", "Ändra antal på offert."),
    ("Webshop", "Webshop", "Medium", "Hjälp i webshopen: original som ska gå att beställa, loggor som ska synas."),
    ("Produktfråga", "Produktfråga", "Medium", "Produktfrågor om allt möjligt."),
    ("Faktura", "Övrigt", "Low", "Fakturafrågor — vidare till ekonomi."),
    ("Service maskin", "Övrigt", "Low", "Service- och maskinfrågor — vidare till Itek."),
    ("Övrigt", "Övrigt", "Medium", "Allt som inte passar någon annan typ."),
]


def execute():
    for label, group, priority, description in ORDER_DESK_TYPES:
        if frappe.db.exists("HD Ticket Type", label):
            continue
        frappe.get_doc(
            {
                "doctype": "HD Ticket Type",
                "name": label,
                "classification_group": group,
                # The priority a type carries is what set_priority falls back to
                # when no keyword rule matched, so this is the coordinator's
                # "gulmarkeras, prioriteras" expressed once.
                "priority": priority if frappe.db.exists("HD Ticket Priority", priority) else None,
                "description": description,
            }
        ).insert(ignore_permissions=True)
