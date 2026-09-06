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

    from helpdesk.api.translation import hold_untranslated_reply

    frappe.has_permission("HD Ticket", "write", doc=ticket_id, throw=True)

    # LANG-08: a ticket answered in another language must not receive the
    # working language straight off the editor. The reply is not lost — it is
    # drafted into the customer’s language and left waiting for the review that
    # `reply_translated` performs, which is the door this send should have used.
    held = hold_untranslated_reply(ticket_id, message)
    if held:
        # The refusal below rolls the request back, and the draft must survive
        # it: a reply that is stopped and then thrown away is a reply the agent
        # has to write twice. Nothing else has been written at this point, so
        # the commit persists the draft and nothing more.
        frappe.db.commit()
        frappe.throw(
            _(
                "Kunden läser {0}. Svaret är översatt och väntar på din "
                "granskning — läs översättningen och skicka den."
            ).format(held.get("target_language") or _("ett annat språk")),
            title=_("Svaret är inte översatt"),
        )

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

    # Locked for update: two presses of the same button must not both pass
    # the "already sent" guard below and send the customer the mail twice.
    doc = frappe.get_doc("HD Message Translation", translation_id, for_update=True)
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
    # Wave 25b (LANG-09): the archive copy of a reply an agent already sent in
    # the customer's language. The customer has their answer; sending this row
    # would mail them a machine's rendering of it, from the one door whose whole
    # promise is that pressing send is a review.
    if doc.get("sent_side") == "Original":
        frappe.throw(
            _(
                "Det här är husets arbetsspråkskopia av ett svar som redan "
                "skickats på kundens språk. Kunden har fått svaret — kopian "
                "finns för arkivet och kan inte skickas."
            ),
            title=_("Arkivkopia"),
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

    # The marks land before the mail leaves. Through the Wave 4 endpoints, so
    # the refusal they encode keeps guarding every other caller and the review
    # carries the agent's name; review first, because mark_translation_sent
    # refuses an unreviewed row. If the send fails the request rolls back and
    # the marks go with it; done the other way round, a failure after the mail
    # had gone would leave a sent mail beside a row that says unsent.
    review_translation(translation_id)
    row = mark_translation_sent(translation_id)

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

    # Link the row to the message it was sent as, so the thread can show
    # which mail the translation belongs to.
    if (
        communication
        and frappe.get_meta("HD Message Translation").has_field("message")
        and not doc.message
    ):
        frappe.db.set_value(
            "HD Message Translation", translation_id, "message", communication
        )

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


@frappe.whitelist()
@agent_only
def delivery_state(ticket_id: str) -> list[dict]:
    """Say, per outgoing message, whether the mail actually left.

    The desk renders the Communication, and a Communication says nothing about
    delivery: it exists as soon as the reply is composed. With Frappe's
    outgoing queue suspended — the way the demo runs — a mail can sit in Email
    Queue for days while the thread shows it as sent. This reads the queue
    instead and reports both the raw status, so an administrator can chase the
    row, and a decided `held`, so the thread needs no status vocabulary of its
    own.

    A message with no queue row is unknown, not waiting: older messages predate
    the queue and cleanup jobs remove rows, so absence of evidence stays absent
    (`held` 0, `status` None) rather than becoming a wall of false warnings.
    """
    frappe.has_permission("HD Ticket", "read", doc=ticket_id, throw=True)

    # Only real messages: the thread renders communication_type "Communication"
    # and nothing else, so an unsent auto-acknowledgement must not produce a
    # warning against a row nobody can see.
    messages = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "communication_type": "Communication",
            "sent_or_received": "Sent",
        },
        fields=["name"],
        order_by="creation asc",
        pluck="name",
    )
    if not messages:
        return []

    # One query for the whole thread: a busy ticket must not cost a round trip
    # per message. One Communication can own several Email Queue rows — Frappe
    # splits a send per recipient when there are many — and those siblings are
    # not retries of one another, so no single row may speak for the message.
    # Every row is folded into one decided state: the worst one wins.
    # frappe.get_all ignores permissions; the HD Ticket read check above is
    # what authorises reading the queue rows here.
    statuses: dict[str, set[str]] = {}
    for row in frappe.get_all(
        "Email Queue",
        filters={"communication": ["in", messages]},
        fields=["communication", "status"],
    ):
        statuses.setdefault(row.communication, set()).add(row.status)

    return [
        {
            "message": name,
            "status": _decide_queue_status(statuses.get(name)),
            # Held means "waiting for a human to send it": only "Not Sent".
            # "Partially Sent" already reached someone, and telling the agent
            # to hand-send it again would double the mail; "Error" and
            # "Expired" are not waiting either, but the status names them so
            # the thread does not render them as delivered.
            "held": 1 if "Not Sent" in statuses.get(name, ()) else 0,
        }
        for name in messages
    ]


# Precedence when one message owns several queue rows: any row still unsent
# outranks every other, then the failures, then the partial and in-flight
# states. Only a message whose every row went out is "Sent".
_QUEUE_STATUS_PRECEDENCE = ("Not Sent", "Error", "Expired", "Partially Sent", "Sending")


def _decide_queue_status(found: set[str] | None) -> str | None:
    if not found:
        return None
    for status in _QUEUE_STATUS_PRECEDENCE:
        if status in found:
            return status
    return "Sent"
