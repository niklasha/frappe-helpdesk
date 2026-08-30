import frappe
from frappe import _
from frappe.utils import now_datetime

from helpdesk.utils import agent_only, get_customers

CHAT_SYSTEM = "chat"

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


def _get_chat(external_conversation_id):
    """Load the conversation recorded under this external id, refusing an unknown one."""
    name = frappe.db.get_value(
        "HD Chat Conversation",
        {"external_conversation_id": external_conversation_id},
        "name",
    )
    if not name:
        frappe.throw(
            _("No chat conversation is recorded for {0}.").format(
                external_conversation_id
            )
        )
    return frappe.get_doc("HD Chat Conversation", name)


def _find_contact(number):
    """Find the contact whose telephone or mobile number is the given number."""
    if not number:
        return None
    for fieldname in ("phone", "mobile_no"):
        name = frappe.db.get_value("Contact", {fieldname: number}, "name")
        if name:
            return name
    return None


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


@frappe.whitelist()
@agent_only
def chat_widget_settings():
    """Return the website chat widget configuration held by the chat external system."""
    row = frappe.db.get_value(
        "HD External System",
        {"system": CHAT_SYSTEM, "enabled": 1},
        ["system", "label"],
        as_dict=True,
    )
    if not row:
        return {"enabled": 0, "greeting": None, "system": CHAT_SYSTEM}
    return {
        "enabled": 1,
        "greeting": row.label or row.system,
        "system": row.system,
    }


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


@frappe.whitelist(methods=["POST"])
@agent_only
def match_call_customer(external_call_id):
    """Match a call to the contact and the customer its calling number belongs to."""
    doc = _get_call(external_call_id)
    contact = _find_contact(doc.from_number)
    if not contact:
        return doc.as_dict()
    doc.contact = contact
    customers = get_customers(contact=contact)
    if customers:
        doc.customer = customers[0]
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def record_chat(
    external_conversation_id, visitor=None, visitor_email=None, transcript=None
):
    """Persist a website chat conversation, replayable by its external id."""
    existing = frappe.db.get_value(
        "HD Chat Conversation",
        {"external_conversation_id": external_conversation_id},
        "name",
    )
    if existing:
        return frappe.get_doc("HD Chat Conversation", existing).as_dict()
    doc = frappe.get_doc(
        {
            "doctype": "HD Chat Conversation",
            "external_conversation_id": external_conversation_id,
            "visitor": visitor,
            "visitor_email": visitor_email,
            "transcript": transcript,
            "started_on": now_datetime(),
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist(methods=["POST"])
@agent_only
def create_ticket_from_chat(external_conversation_id, subject=None):
    """Turn a chat conversation into a ticket, keeping the ticket it already has."""
    doc = _get_chat(external_conversation_id)
    if doc.ticket:
        return doc.as_dict()
    values = {
        "doctype": "HD Ticket",
        "subject": subject
        or _("Chat with {0}").format(doc.visitor or doc.external_conversation_id),
        "description": doc.transcript or "",
    }
    if doc.visitor_email:
        values["raised_by"] = doc.visitor_email
    created = frappe.get_doc(values)
    created.insert(ignore_permissions=True)
    doc.ticket = created.name
    doc.save(ignore_permissions=True)
    return doc.as_dict()
