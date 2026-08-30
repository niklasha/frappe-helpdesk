import frappe

from helpdesk.utils import agent_only

CALL_VALUE_FIELDS = (
    "from_number",
    "to_number",
    "started_on",
    "duration",
    "recording_url",
    "transcript",
)


@frappe.whitelist(methods=["POST"])
@agent_only
def record_call(external_call_id, direction="Inbound", **values):
    """Persist a call handed over by a telephony system, replayable by its external id."""
    existing = frappe.db.get_value(
        "HD Call Record", {"external_call_id": external_call_id}, "name"
    )
    if existing:
        return frappe.get_doc("HD Call Record", existing).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD Call Record",
            "external_call_id": external_call_id,
            "direction": direction or "Inbound",
            **{
                field: values.get(field)
                for field in CALL_VALUE_FIELDS
                if values.get(field) is not None
            },
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()
