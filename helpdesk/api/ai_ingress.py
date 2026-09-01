"""Let an inbound ticket be looked at without anyone asking.

Every AI capability this helpdesk has was reachable only by an agent calling an
endpoint that nothing in the interface calls. The engines worked and the tickets
sat untouched. This module is the caller: one queued job per new ticket that
detects the language, translates what is not in the working language, and
triages the result.

Three properties matter more than the feature:

* **Queued.** A model call inside `after_insert` makes every inbound email wait
  on a third party, and a timeout there fails the ticket creation — the message
  is then lost rather than merely un-triaged.
* **Governed.** It ships off. One model call per inbound email is real money at
  real volume, and turning that on is a decision somebody makes.
* **Harmless when it fails.** A provider that will not answer costs a triage,
  never the ticket.
"""

import frappe
from frappe.utils import strip_html

from helpdesk.api import ai_runner, ai_triage, translation
from helpdesk.utils import agent_only, is_admin

SETTING = "ai_triage_incoming"

# One job per ticket, keyed on it. Frappe drops a job whose id is already
# queued, which is the cheap half of not paying for the same triage twice.
JOB_PREFIX = "helpdesk-ai-ingress"


def ingress_enabled() -> bool:
    """Whether an inbound ticket should be looked at by a model at all."""
    return bool(frappe.db.get_single_value("HD Settings", SETTING))


@frappe.whitelist(methods=["POST"])
@agent_only
def set_ingress_enabled(enabled: int | bool) -> bool:
    """Turn the automation on or off. Administrator only: it spends money."""
    if not is_admin():
        frappe.throw(
            frappe._("Only an administrator may change the AI ingress setting."),
            frappe.PermissionError,
        )
    frappe.db.set_single_value("HD Settings", SETTING, 1 if int(enabled) else 0)
    return ingress_enabled()


def enqueue_for_ticket(doc, method=None) -> None:
    """Hook target: queue one ingress job for a ticket that just arrived.

    Deliberately does nothing itself. Whatever is wrong downstream — no engine,
    no runner, a provider refusing — must not reach the caller, because the
    caller here is `HD Ticket.after_insert` and its failure is a lost message.
    """
    if not ingress_enabled():
        return
    try:
        frappe.enqueue(
            "helpdesk.api.ai_ingress.run_ingress",
            queue="short",
            # job_id, not job_name: deduplication is keyed on the id and Frappe
            # throws outright when it is missing. A job_name looks like it would
            # do and quietly does not.
            job_id=f"{JOB_PREFIX}-{doc.name}",
            deduplicate=True,
            ticket_id=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        # The ticket exists; that it went un-triaged is worth a log and nothing
        # more. Raising here would undo the insert.
        #
        # With the traceback: the first version of this logged a sentence, and a
        # sentence is what you get instead of the reason when the enqueue itself
        # is what went wrong.
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"could not queue ingress for {doc.name}\n\n{frappe.get_traceback()}",
        )


def enqueue_for_message(doc, method=None) -> None:
    """Hook target: queue a translation for a customer reply that just arrived.

    Wave 12 translated the ticket and stopped there, so an agent read a Swedish
    opening and English replies below it — worse than an honest English thread,
    because it looks finished.

    Everything the ticket hook is careful about applies here for the same
    reasons: queued so the email never waits on a provider, governed by the same
    switch because the bill grows per message, and silent on failure because a
    lost translation must never cost the reply itself.
    """
    if doc.reference_doctype != "HD Ticket" or not doc.reference_name:
        return
    # Only what a customer sent us. Our own replies are written in the working
    # language already, and outbound translation is a different endpoint with a
    # different target.
    if doc.sent_or_received != "Received":
        return
    if not ingress_enabled():
        return
    try:
        frappe.enqueue(
            "helpdesk.api.ai_ingress.translate_message",
            queue="short",
            job_id=f"{JOB_PREFIX}-message-{doc.name}",
            deduplicate=True,
            message=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"could not queue translation for {doc.name}\n\n{frappe.get_traceback()}",
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_message(message: str) -> dict:
    """Put one customer message into the working language, beside its original.

    Runs in a worker for the queued path and inline for an agent who asks. Both
    are safe to repeat: the idempotency key is the message, so a retry replays
    the first answer rather than buying a second.
    """
    done = {"message": message, "translated": False, "reason": None}
    row = frappe.db.get_value(
        "Communication",
        message,
        ["reference_doctype", "reference_name", "content", "sent_or_received"],
        as_dict=True,
    )
    if not row or row.reference_doctype != "HD Ticket" or not row.reference_name:
        done["reason"] = "not a ticket message"
        return done
    if row.sent_or_received != "Received":
        done["reason"] = "not from the customer"
        return done

    ticket_id = row.reference_name
    text = strip_html(row.content or "").strip()
    if not text:
        done["reason"] = "nothing to translate"
        return done

    working = translation.get_working_language()
    source = translation.detect_language_code(text)
    if not source or source == working or not _is_supported(source):
        done["reason"] = "already readable"
        return done

    # When an email opens a ticket, its words become both the ticket description
    # and the first Communication. The ingress has already translated the
    # description, so translating this message would buy the same sentence
    # twice and show it twice. Adopting that translation costs nothing and is
    # what makes the opening email render like every later one.
    adopted = frappe.db.get_value(
        "HD Message Translation",
        {
            "ticket": ticket_id,
            "direction": "Inbound",
            "original_text": text,
            "message": ["in", ("", None)],
        },
        "name",
    )
    if adopted:
        frappe.db.set_value("HD Message Translation", adopted, "message", message)
        done.update(translated=True, reason="adopted the ticket's own translation")
        return done

    try:
        translation.generate_inbound_translation(
            ticket_id=ticket_id,
            original_text=text,
            target_language=working,
            message=message,
            idempotency_key=f"ingress-translate-message-{message}",
        )
        done["translated"] = True
    except Exception:
        done["reason"] = "the provider did not answer"
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"translation failed for message {message}\n\n{frappe.get_traceback()}",
        )
    return done


def run_ingress(ticket_id: str) -> dict:
    """Detect, translate and triage one ticket. Runs in a worker, not a request.

    Each step is attempted on its own: a translation that fails is not a reason
    to skip the triage, and neither is a reason to leave a mark on the ticket.
    """
    done = {"ticket": ticket_id, "translated": False, "triaged": False}
    if not ai_runner.is_runner_available():
        return done

    text = _ticket_text(ticket_id)
    working = translation.get_working_language()
    source = translation.detect_language_code(text)

    # Only a language we know we support, and only when it is not the one agents
    # already read. Translating Swedish into Swedish is waste and noise.
    if source and source != working and _is_supported(source):
        try:
            translation.generate_inbound_translation(
                ticket_id=ticket_id,
                original_text=text,
                target_language=working,
                idempotency_key=f"ingress-translate-{ticket_id}",
            )
            done["translated"] = True
        except Exception:
            frappe.log_error(
                title="Helpdesk AI ingress", message=f"translation failed for {ticket_id}"
            )

    try:
        ai_triage.triage_ticket(
            ticket_id=ticket_id,
            # The key is the ticket, so a job that runs twice — a retry, a second
            # save — replays the first answer instead of buying another.
            idempotency_key=f"ingress-triage-{ticket_id}",
        )
        done["triaged"] = True
    except Exception:
        frappe.log_error(
            title="Helpdesk AI ingress", message=f"triage failed for {ticket_id}"
        )
    return done


@frappe.whitelist(methods=["POST"])
@agent_only
def triage_incoming_ticket(ticket_id: str) -> dict:
    """Run the ingress chain for one ticket now, for an agent who asks.

    The same idempotency keys apply, so asking twice is free and asking after the
    automation already ran returns what it produced.
    """
    return run_ingress(ticket_id)


def _ticket_text(ticket_id: str) -> str:
    """The text a model should read: what the customer actually wrote."""
    from helpdesk.api import ai_generation

    return ai_generation.ticket_text(ticket_id)


def _is_supported(language_code: str) -> bool:
    """A language the company has enabled — the catalogue decides, not the model."""
    return bool(
        frappe.db.exists(
            "HD Supported Language", {"language_code": language_code, "enabled": 1}
        )
    )
