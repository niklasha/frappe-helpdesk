import json

import frappe
from frappe.utils import now_datetime

from helpdesk.utils import agent_only


@frappe.whitelist(methods=["POST"])
@agent_only
def record_extraction(ticket_id, idempotency_key=None, **values):
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
def correct_extraction(extraction_id, corrections, reason=None):
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
