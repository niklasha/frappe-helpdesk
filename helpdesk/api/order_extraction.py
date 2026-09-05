import json

import frappe
from frappe import _
from frappe.utils import now_datetime

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
def extract_order(ticket_id: str, idempotency_key: str | None = None) -> dict:
    """Read one ticket's order details with the AI engine, and record them.

    Only the details the customer stated are taken from the answer. Whether
    they add up to an order stays a helpdesk decision: `record_extraction`
    derives completeness from the required fields, so an engine that declares
    an order ready cannot make it so.

    A key that already produced an extraction returns it unchanged, without
    asking the engine a question it has answered once already.
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
        ai_generation.ticket_text(ticket_id),
        EXTRACTION_SCHEMA,
        EXTRACTION_FIELDS,
    )
    details = {field: answer[field] for field in EXTRACTION_FIELDS if field in answer}
    generation = ai_generation.provenance(response, prompt_version, engine)
    result = record_extraction(
        ticket_id=ticket_id,
        idempotency_key=idempotency_key,
        **generation,
        **details,
    )
    ai_generation.attribute(
        "extracted an order", "HD Order Extraction", result["name"], generation
    )
    return result


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


@frappe.whitelist(methods=["POST"])
@agent_only
def approve_extraction(extraction_id: str) -> dict:
    """"Använd i order": an agent vouches for a complete extraction.

    The row is marked ready for the connector with the agent's name and time
    on it, and the audit log gets an event. Nothing is sent anywhere: the ERP
    submission is made by `erp_order.submit_order` when a person submits, so
    this endpoint must never create an HD External Order Submission.
    """
    # Local import: the audit log is the only thing this endpoint shares with
    # governance, and order_extraction is imported by the ingress chain.
    from helpdesk.api import governance

    doc = frappe.get_doc("HD Order Extraction", extraction_id)
    frappe.has_permission("HD Ticket", "read", doc=doc.ticket, throw=True)
    missing = json.loads(doc.missing_fields or "[]")
    if missing or not doc.complete:
        frappe.throw(
            _("Beställningsunderlaget är ofullständigt; följande saknas: {0}").format(
                ", ".join(missing) or _("okänt")
            )
        )
    doc.approved_by = frappe.session.user
    doc.approved_on = now_datetime()
    doc.ready_for_connector = 1
    doc.status = "Ready to create order"
    doc.save(ignore_permissions=True)
    governance.log_automation_event(
        "approve_extraction",
        actor="User",
        reference_doctype="HD Order Extraction",
        reference_name=doc.name,
        details={"ticket": doc.ticket},
    )
    return doc.as_dict()
