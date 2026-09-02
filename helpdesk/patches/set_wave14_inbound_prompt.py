"""Bring an untouched inbound-translation fragment up to the combined call.

Wave 9 seeded `translation_inbound` into the prompt library as a copy of the
built-in wording of its day, and Wave 14 changed what the call does: the engine
now detects the language and answers JSON, or declines when the text is already
the working language. The built-in wording followed; the seeded row did not,
because seeding never touches a row that exists. The live demo showed the
result: the schema hint asked for JSON, the released fragment asked for
"the finished text and nothing else", and the model happened to follow the
hint. Two instructions in conflict is not a state to leave a site in, and an
administrator opening the fragment read a description of a call that no longer
exists.

Only a fragment still holding the seeded wording verbatim is changed. An
administrator who edited it has said something this patch has no business
overruling — the same rule the language-name backfill and the seed itself
follow. The change goes through the document so the controller bumps the
version: generations made under the old wording carry the old number, and an
audit has to be able to tell them apart.
"""

import frappe

from helpdesk.api.ai_generation import PROMPTS, TRANSLATION_INBOUND

# Verbatim what Wave 9 seeded. A literal rather than a reconstruction from the
# constants, because this is a historical fact about what sites hold, not a
# function of what the constants say today.
SEEDED_WORDING = (
    "You translate helpdesk correspondence for a Swedish print shop. Keep the "
    "meaning, the tone and every order number, size and date exactly as they "
    "stand, and translate nothing that is already in the target language. "
    "This translation is read by a colleague deciding what to do, so stay "
    "literal where literal and fluent disagree.\n\n"
    "Answer with the finished text and nothing else: no preamble, no "
    "explanation, and no detail the material you were given does not support."
)


def execute():
    if not frappe.db.exists("HD AI Prompt", TRANSLATION_INBOUND):
        return
    doc = frappe.get_doc("HD AI Prompt", TRANSLATION_INBOUND)
    if (doc.prompt or "").strip() != SEEDED_WORDING.strip():
        return
    current = PROMPTS[TRANSLATION_INBOUND]
    doc.prompt = current["prompt"]
    doc.purpose = current["purpose"]
    doc.flags.ignore_permissions = True
    doc.save()
