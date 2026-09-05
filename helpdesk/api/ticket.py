import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin


@frappe.whitelist()
@agent_only
def bulk_reply(ticket_ids: list, message: str, attachments: list | None = None):

    if not ticket_ids:
        return

    # dedupe but keep the order the agent picked. set() orders by hash, which varies
    # per process, and duplicates would attach the same file to a ticket twice
    ticket_ids = list(dict.fromkeys(ticket_ids))

    # Check every ticket before writing anything, so one ticket the agent cannot
    # reply to does not leave the rest of the batch half done
    tickets = []
    for ticket_id in ticket_ids:
        frappe.has_permission("HD Ticket", "write", doc=ticket_id, throw=True)
        tickets.append(frappe.get_doc("HD Ticket", ticket_id))

    link_attachments_to_tickets(attachments, ticket_ids)

    for doc in tickets:
        try:
            doc.reply_via_agent(
                message, to=doc.raised_by, attachments=attachments or []
            )
        except Exception as e:
            frappe.log_error(
                title=f"Bulk reply failed for ticket {doc.name}",
                message=str(e),
            )


def link_attachments_to_tickets(attachments: list | None, ticket_ids: list):
    if not attachments:
        return
    if not ticket_ids:
        return

    # only one attachment is created, but does not refer to any doctype/docname until now. Link it to all the tickets in context.
    # Done because, FileUploader only handles for one file, and cant upload to multiple doctypes/docnames at the same time.
    for a in attachments:
        file_doc = frappe.get_doc("File", a)
        file_doc.attached_to_doctype = "HD Ticket"
        file_doc.attached_to_name = ticket_ids[0]
        file_doc.save()

    for ticket_id in ticket_ids[1:]:
        for a in attachments:
            file_doc = frappe.get_doc("File", a)
            new_file_doc = frappe.copy_doc(file_doc)
            new_file_doc.attached_to_name = ticket_id
            new_file_doc.save()


def assign_ticket_to_agent(ticket_id, agent_id=None):
    if not ticket_id:
        return

    ticket_doc = frappe.get_doc("HD Ticket", ticket_id)

    if not agent_id:
        # assign to self
        agent_id = frappe.session.user

    if not frappe.db.exists("HD Agent", agent_id):
        frappe.throw(_("Tickets can only be assigned to agents"))

    ticket_doc.assign_agent(agent_id)
    return ticket_doc


@frappe.whitelist(methods=["POST"])
@agent_only
def toggle_ticket_favorite(ticket_id: str, favorite: int | bool = 1):
    """Add or remove the current user's favorite marker for a ticket."""
    if not ticket_id:
        frappe.throw(_("A ticket is required"))
    ticket = frappe.get_doc("HD Ticket", ticket_id)
    frappe.has_permission("HD Ticket", "read", doc=ticket, throw=True)
    user = frappe.session.user
    existing = frappe.db.get_value(
        "HD Ticket Favorite", {"ticket": ticket.name, "user": user}, "name"
    )
    if cint(favorite):
        if not existing:
            frappe.get_doc(
                {"doctype": "HD Ticket Favorite", "ticket": ticket.name, "user": user}
            ).insert(ignore_permissions=True)
    elif existing:
        frappe.delete_doc("HD Ticket Favorite", existing, ignore_permissions=True)
    return {"favorite": bool(cint(favorite))}


@frappe.whitelist()
def delete_ticket(name: str):
    if not is_admin():
        frappe.throw(
            msg=_("Only administrators can delete tickets."),
            title=_("Not Allowed"),
            exc=frappe.PermissionError,
        )
    frappe.delete_doc("HD Ticket", name, force=True, ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
@agent_only
def reply_and_set_status(
    ticket_id: str,
    message: str,
    status: str,
    to: str | None = None,
    cc: str | None = None,
    bcc: str | None = None,
    attachments: list | str | None = None,
    from_email: dict | str | None = None,
) -> dict:
    """Send an agent reply and move the ticket to `status` in one action.

    The reply goes through the same path the editor's Send button uses
    (HD Ticket.reply_via_agent), so it lands as a Sent Communication on the
    thread. The status is checked before anything is sent: a status the site
    has not defined must not cost the customer a message.
    """
    if isinstance(attachments, str):
        attachments = frappe.parse_json(attachments) or []
    if isinstance(from_email, str):
        from_email = frappe.parse_json(from_email) or None

    status = (status or "").strip()
    if not status or not frappe.db.exists("HD Ticket Status", status):
        frappe.throw(
            _("Statusen '{0}' finns inte. Välj en befintlig ärendestatus.").format(
                status
            ),
            title=_("Okänd status"),
        )

    frappe.has_permission("HD Ticket", "write", doc=ticket_id, throw=True)
    ticket = frappe.get_doc("HD Ticket", ticket_id)

    ticket.reply_via_agent(
        message,
        from_email=from_email,
        to=to or ticket.raised_by,
        cc=cc,
        bcc=bcc,
        attachments=attachments or [],
    )

    communication = frappe.db.get_value(
        "Communication",
        {
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "sent_or_received": "Sent",
        },
        "name",
        order_by="creation desc",
    )

    # save() rather than db.set_value so SLA and activity hooks see the change
    ticket.reload()
    if ticket.status != status:
        ticket.status = status
        ticket.save()

    return {"communication": communication, "status": status}


@frappe.whitelist(methods=["POST"])
@agent_only
def reply_translated(
    ticket_id: str, translation_id: str, status: str | None = None
) -> dict:
    """Send a reviewed translation to the customer in the customer's language.

    Wave 4 forbids sending an outbound translation nobody reviewed, and that
    rule stays. This is the one door that reviews and sends in the same motion:
    the agent reading the draft and pressing send *is* the review, so the audit
    names them, and both marks land together — no row can end up sent but
    unreviewed, or reviewed by nobody.

    The Swedish the agent wrote stays on the row as `original_text`, so the
    thread can still show what was meant beside what was said.
    """
    from helpdesk.api.translation import mark_translation_sent, review_translation

    frappe.has_permission("HD Ticket", "write", doc=ticket_id, throw=True)

    doc = frappe.get_doc("HD Message Translation", translation_id)
    if doc.ticket != ticket_id:
        frappe.throw(
            _("Översättningen hör till ett annat ärende och kan inte skickas här."),
            title=_("Fel ärende"),
        )
    if doc.direction != "Outbound":
        frappe.throw(
            _("Bara en översättning av ett svar kan skickas till kunden."),
            title=_("Fel riktning"),
        )
    if doc.sent_on:
        frappe.throw(
            _("Översättningen är redan skickad till kunden."),
            title=_("Redan skickad"),
        )

    message = (doc.translated_text or "").strip()
    if not message:
        frappe.throw(
            _("Översättningen är tom och kan inte skickas."),
            title=_("Tom översättning"),
        )

    # Check the status before anything is sent: a status the site has not
    # defined must not cost the customer a message.
    status = (status or "").strip()
    if status and not frappe.db.exists("HD Ticket Status", status):
        frappe.throw(
            _("Statusen '{0}' finns inte. Välj en befintlig ärendestatus.").format(
                status
            ),
            title=_("Okänd status"),
        )

    ticket = frappe.get_doc("HD Ticket", ticket_id)
    ticket.reply_via_agent(message, to=ticket.raised_by)

    communication = frappe.db.get_value(
        "Communication",
        {
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "sent_or_received": "Sent",
        },
        "name",
        order_by="creation desc",
    )

    # Through the Wave 4 endpoints, so the refusal they encode keeps guarding
    # every other caller and the review carries the agent's name.
    review_translation(translation_id)
    row = mark_translation_sent(translation_id)

    if status:
        # save() rather than db.set_value so SLA and activity hooks see it
        ticket.reload()
        if ticket.status != status:
            ticket.status = status
            ticket.save()

    return {
        "communication": communication,
        "translation": translation_id,
        "reviewed_by": row.get("reviewed_by"),
        "sent_on": row.get("sent_on"),
        "status": status or ticket.status,
    }
