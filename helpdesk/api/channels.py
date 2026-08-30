import frappe
from frappe import _

from helpdesk.utils import agent_only

CALL_VALUE_FIELDS = (
    "provider",
    "from_number",
    "to_number",
    "started_on",
    "duration",
    "recording_url",
    "transcript",
)


def _get_call(external_call_id):
    """Load the call recorded under this external id, refusing an unknown call."""
    name = frappe.db.get_value(
        "HD Call Record", {"external_call_id": external_call_id}, "name"
    )
    if not name:
        frappe.throw(_("No call is recorded for {0}.").format(external_call_id))
    return frappe.get_doc("HD Call Record", name)


@frappe.whitelist()
@agent_only
def telephony_settings():
    """Return the configured telephony systems, so a PBX is set up rather than coded in."""
    return frappe.get_all(
        "HD External System",
        filters={"enabled": 1, "system": ["like", "telephony%"]},
        fields=["name", "system", "label", "link_template"],
        order_by="system asc",
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


@frappe.whitelist(methods=["POST"])
@agent_only
def record_call_transcript(external_call_id, transcript):
    """Store the transcription a telephony system produced for a recorded call."""
    doc = _get_call(external_call_id)
    doc.transcript = transcript
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def create_ticket_from_call(external_call_id, subject=None, ticket=None):
    """Turn a call into a ticket, keeping the ticket a call was already given."""
    doc = _get_call(external_call_id)
    if doc.ticket:
        return doc.as_dict()
    if not ticket:
        created = frappe.get_doc(
            {
                "doctype": "HD Ticket",
                "subject": subject
                or _("Call from {0}").format(doc.from_number or doc.external_call_id),
                "description": doc.transcript or "",
            }
        )
        created.insert(ignore_permissions=True)
        ticket = created.name
    doc.ticket = ticket
    doc.save(ignore_permissions=True)
    return doc.as_dict()
