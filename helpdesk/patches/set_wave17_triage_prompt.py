"""Write the approval-with-changes rule into an untouched triage fragment.

The demo filed "godkänner offerten, men gör västarna gröna" as Offertändring.
The released fragment named both labels and said only that an approval of a
proof is Korrektur godkänt; nobody had told the model that an approval with
changes is still an approval, or that a change of price or quantity with no
proof in play is the quote change. Wave 17 puts the coordinator's rule where
the model reads it (`TRIAGE_APPROVAL_RULE`), in the built-in wording and in
the order-desk seed alike.

Only a fragment still holding one of the two seeded wordings verbatim is
changed: the built-in wording a site copied when it first released the
prompt, or the order-desk wording `ops/seed_order_desk.py` wrote. An
administrator who edited the fragment has said something this patch has no
business overruling — the rule `set_wave14_inbound_prompt` follows. The change
goes through the document so the controller bumps the version and the change
log keeps the earlier wording: generations made under it carry the old number,
and an audit has to be able to read what the model was told at the time.
"""

import frappe

from helpdesk.api.ai_generation import PROMPTS, TICKET_TRIAGE, TRIAGE_APPROVAL_RULE

# Verbatim what sites hold. Literals rather than reconstructions, because
# these are historical facts about released rows, not functions of what the
# constants say today.
BUILT_IN_WORDING = (
    "You triage incoming messages for a Swedish print shop's helpdesk. "
    "Read the ticket and judge what it is about, how urgent it is, and "
    "what a colleague would need to know before picking it up. Base "
    "every field on what the ticket actually says.\n\n"
    "Answer with one JSON object and nothing else. Leave a field out entirely "
    "when the message does not say what it should be; never invent a value."
)

STARK_ACCOUNTS = "Swedol, Blåkläder, Prevex, Ahlsell and Procurator"

ORDER_DESK_WORDING = (
    "You triage incoming messages for Applitron's order desk. Read the ticket "
    "and judge what it is about, how urgent it is, and what a colleague needs "
    "to know before picking it up. Base every field on what the ticket "
    "actually says.\n\n"
    "classification: exactly one of these labels, written as here: DEX, "
    "Express, Reklamation, Skapa order, Korrektur godkänt, Omritning Indien, "
    "Vectorizer, Original hos annan kund, DHL express, Hör ihop, Webinlogg, "
    "Inväntar frakt, Leveransfråga, Paketsökning, Offertändring, Webshop, "
    "Produktfråga, Faktura, Service maskin, Övrigt. A purchase order or a "
    "customer we place orders for without a proof is Skapa order. An approval "
    "of a proof is Korrektur godkänt. A mail continuing an earlier thread from "
    "the same customer is Hör ihop.\n\n"
    "priority: Urgent for DEX production. High for Express production, for "
    "every complaint, and for DHL express shipping (DADOM, DADOT). Medium "
    "otherwise. Low only for a question with no order behind it.\n\n"
    "suggested_agent: leave empty unless the message names the colleague who "
    "already handles the case — a case stays with the person who started it.\n\n"
    "missing_information: what the desk still needs before a proof can be "
    "made — size, product, quantity, and a vectorised logo file. Colour is "
    f"optional. For {STARK_ACCOUNTS} the product is Stark unless they say "
    "otherwise; never list product as missing for them. JPG and PNG are never "
    "vector, and EPS, AI, PDF and SVG often are not — say so when the customer "
    "relies on one.\n\n"
    "complaint: true for a Reklamation. repeat_order: true when the customer "
    "refers to a previous order, an existing article, or a logo we already hold."
)

# The order-desk wording with the rule in the paragraph that names the labels,
# exactly as ops/seed_order_desk.py now writes it.
ORDER_DESK_MARKER = "the same customer is Hör ihop.\n\n"
ORDER_DESK_WITH_RULE = ORDER_DESK_WORDING.replace(
    ORDER_DESK_MARKER, ORDER_DESK_MARKER + TRIAGE_APPROVAL_RULE + "\n\n", 1
)


def execute():
    if not frappe.db.exists("HD AI Prompt", TICKET_TRIAGE):
        return
    doc = frappe.get_doc("HD AI Prompt", TICKET_TRIAGE)
    held = (doc.prompt or "").strip()
    if held == BUILT_IN_WORDING.strip():
        doc.prompt = PROMPTS[TICKET_TRIAGE]["prompt"]
    elif held == ORDER_DESK_WORDING.strip():
        doc.prompt = ORDER_DESK_WITH_RULE
    else:
        return
    doc.flags.ignore_permissions = True
    doc.save()
