import json

import frappe
from frappe.utils import now_datetime, strip_html

from helpdesk.api import ai_generation
from helpdesk.utils import agent_only

EXTRACTION_SCHEMA = (
    "Use these keys and no others: product, quantity (a number), size, colors, "
    "production_option, delivery_information, original_files. Do not say "
    "whether the order is complete or ready; that is not yours to judge."
)

EXTRACTION_FIELDS = (
    "product",
    "quantity",
    "size",
    "colors",
    "production_option",
    "delivery_information",
    "original_files",
)

# What the order card reads off an extraction. Named rather than as_dict so the
# card is not handed provenance and token counts it has no column for, and so
# a field the doctype gains later (approved_by, approved_on) is asked for by
# name and comes back empty until it exists.
PANEL_FIELDS = (
    "name",
    "ticket",
    "product",
    "quantity",
    "size",
    "colors",
    "production_option",
    "delivery_information",
    "customer",
    "missing_fields",
    "required_fields",
    "complete",
    "status",
    "ready_for_connector",
    "approved_by",
    "approved_on",
    "corrections",
    "source_message",
    "ai_cost",
    "cost_known",
    "creation",
)


@frappe.whitelist(methods=["POST"])
@agent_only
def record_extraction(
    ticket_id: str, idempotency_key: str | None = None, **values
) -> dict:
    """Persist structured order extraction and derive completeness server-side."""
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    if idempotency_key:
        name = frappe.db.get_value("HD Order Extraction", {"idempotency_key": idempotency_key}, "name")
        if name:
            return frappe.get_doc("HD Order Extraction", name).as_dict()
    required = values.pop("required_fields", None) or ["customer", "product", "quantity", "size"]
    values.pop("missing_fields", None)
    values.pop("complete", None)
    values.pop("status", None)
    values.pop("ready_for_connector", None)
    if isinstance(required, str):
        required = json.loads(required) if required.startswith("[") else [x.strip() for x in required.split(",") if x.strip()]
    missing = [field for field in required if not values.get(field)]
    complete = not missing
    doc = frappe.get_doc({"doctype": "HD Order Extraction", "ticket": ticket_id, "idempotency_key": idempotency_key, "required_fields": json.dumps(required), "missing_fields": json.dumps(missing), "complete": complete, "status": "Ready to create order" if complete else "Needs Review", "ready_for_connector": complete, "corrections": {}, "corrected_on": None, **values})
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def extract_order(
    ticket_id: str,
    idempotency_key: str | None = None,
    source_message: str | None = None,
) -> dict:
    """Read one ticket's order details with the AI engine, and record them.

    Only the details the customer stated are taken from the answer. Whether
    they add up to an order stays a helpdesk decision: `record_extraction`
    derives completeness from the required fields, so an engine that declares
    an order ready cannot make it so.

    A key that already produced an extraction returns it unchanged, without
    asking the engine a question it has answered once already.

    `source_message` is the customer reply that caused a re-read (Wave 18):
    its words are put in front of the engine after the ticket's, and its name
    is recorded on the row, so the card can say which mail the details came
    from. The earlier row stays — what the model read and when is audit.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    stored = ai_generation.replayed("HD Order Extraction", idempotency_key)
    if stored:
        return frappe.get_doc("HD Order Extraction", stored).as_dict()
    engine = ai_generation.engine_or_throw()
    instructions, prompt_version = ai_generation._prompt(
        ai_generation.ORDER_EXTRACTION
    )
    answer, response = ai_generation.generate_json(
        engine,
        instructions,
        _extraction_text(ticket_id, source_message),
        EXTRACTION_SCHEMA,
        EXTRACTION_FIELDS,
    )
    details = {field: answer[field] for field in EXTRACTION_FIELDS if field in answer}
    generation = ai_generation.provenance(response, prompt_version, engine)
    result = record_extraction(
        ticket_id=ticket_id,
        idempotency_key=idempotency_key,
        source_message=source_message,
        **generation,
        **details,
    )
    ai_generation.attribute(
        "extracted an order", "HD Order Extraction", result["name"], generation
    )
    return result


def _extraction_text(ticket_id: str, source_message: str | None) -> str:
    """The ticket as opened, then the reply that revisits it — in that order.

    The reply alone would be an extraction of a different ticket: "make it 60"
    names no product, and only under the opening mail is it forty vests
    becoming sixty.
    """
    text = ai_generation.ticket_text(ticket_id)
    if not source_message:
        return text
    content = frappe.db.get_value("Communication", source_message, "content")
    reply = strip_html(content or "").strip()
    return f"{text}\n\nKundens svar:\n{reply}" if reply else text


@frappe.whitelist(methods=["GET", "POST"])
@agent_only
def ticket_extraction(ticket_id: str) -> dict | None:
    """The newest extraction on a ticket, for the order card — or nothing.

    Nothing, and not an error, for a ticket that is not an order: the card
    asks the same way for every ticket, and an invoice question has no order
    details to show. Newest by creation, because a customer reply re-reads
    the thread into a new row beside the old one (see `extract_order`), and
    the card shows what the conversation says now.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)
    meta = frappe.get_meta("HD Order Extraction")
    fields = [field for field in PANEL_FIELDS if field == "name" or meta.has_field(field)]
    rows = frappe.get_all(
        "HD Order Extraction",
        filters={"ticket": ticket_id},
        fields=fields,
        order_by="creation desc",
        limit_page_length=1,
    )
    if not rows:
        return None
    row = rows[0]
    for field in ("missing_fields", "required_fields"):
        row[field] = _as_list(row.get(field))
    return row


def _as_list(value) -> list:
    """The JSON list a Small Text field holds, or a list of nothing."""
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [part.strip() for part in str(value).split(",") if part.strip()]
    return parsed if isinstance(parsed, list) else []


@frappe.whitelist(methods=["POST"])
@agent_only
def correct_extraction(
    extraction_id: str,
    corrections: dict | list | str,
    reason: str | None = None,
) -> dict:
    doc = frappe.get_doc("HD Order Extraction", extraction_id)
    if isinstance(corrections, str):
        corrections = json.loads(corrections)
    for field, value in corrections.items():
        if field in doc.meta.get_valid_columns():
            setattr(doc, field, value)
    doc.corrections = corrections
    doc.correction_reason = reason
    doc.corrected_by = frappe.session.user
    doc.corrected_on = now_datetime()
    required = json.loads(doc.required_fields or "[]")
    missing = [field for field in required if not doc.get(field)]
    doc.missing_fields = json.dumps(missing)
    doc.complete = not missing
    doc.status = "Ready to create order" if doc.complete else "Needs Review"
    doc.ready_for_connector = doc.complete
    doc.save(ignore_permissions=True)
    return doc.as_dict()
