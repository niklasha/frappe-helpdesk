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

The opening text of a ticket is a fourth property's worth of care, because it
arrives twice. An email that opens a ticket becomes both the description and
the first Communication, and each of those has a hook that queues a job. Until
Wave 17 the two jobs ran independently, each able to adopt the other's work
and neither knowing whether the other had finished; which one paid was decided
by the worker, and now and then both did. Now the ticket's chain does ticket
then message in sequence under one lock per ticket, and the opening message's
own job takes the same lock and runs the same chain — so whichever is picked
up first does the work once, and the other finds it done.
"""

import frappe
from frappe.utils import strip_html

from helpdesk.api import ai_runner, ai_triage, translation
from helpdesk.utils import agent_only, is_admin

SETTING = "ai_triage_incoming"

# One job per ticket, keyed on it. Frappe drops a job whose id is already
# queued, which is the cheap half of not paying for the same triage twice.
JOB_PREFIX = "helpdesk-ai-ingress"

# The queue both jobs for a new ticket go on. One queue rather than two so that
# a bench with a single worker runs them one after the other, never side by
# side; the lock below is what holds on a bench with several.
QUEUE = "short"

# How long a job waits for the other one's chain to finish before it gives up
# adopting and does the work itself. Generously past a model call, because a
# job that stops waiting early is a job that pays a second time.
LOCK_WAIT_SECONDS = 120
LOCK_EXPIRY_SECONDS = 300


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
            queue=QUEUE,
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

    The opening message is the special case. Helpdesk creates it inside the
    ticket's own `after_insert`, so this hook fires *before* the ticket's, and
    on a single-worker bench its job is the one picked up first. That job is
    keyed on the ticket rather than the message and goes on the same queue as
    the ticket's chain: it is the same work, and `translate_message` knows to
    run the whole chain under the ticket's lock when it finds itself first.
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
    opening = _is_opening_message(doc.name, doc.reference_name)
    try:
        frappe.enqueue(
            "helpdesk.api.ai_ingress.translate_message",
            queue=QUEUE,
            job_id=(
                f"{JOB_PREFIX}-{doc.reference_name}-opening"
                if opening
                else f"{JOB_PREFIX}-message-{doc.name}"
            ),
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

    For the ticket's opening message this never generates on its own. Those
    words are the ticket's description, and the ticket's chain translates them
    once and hands the result to this message. If that chain has not run yet
    when this job is picked up, the job takes the ticket's lock and runs the
    chain itself — every step of it is keyed, so the ticket's own job then
    finds everything replayed and pays nothing. Waiting for the other job
    instead would deadlock a single worker, which is what this bench runs:
    the job in front of the queue would be waiting for the job behind it.
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

    # When an email opens a ticket, its words become both the ticket description
    # and the first Communication. The ingress translates the description, so
    # translating this message would buy the same sentence twice and show it
    # twice. Adopting that translation costs nothing and is what makes the
    # opening email render like every later one.
    if _adopt(ticket_id, message, text):
        done.update(translated=True, reason="adopted the ticket's own translation")
        return done

    if _is_opening_message(message, ticket_id):
        try:
            with _ticket_lock(ticket_id):
                # Under the lock: a chain that finished while we waited for it
                # has left a row to adopt, and one that never ran is run here.
                if _adopt(ticket_id, message, text):
                    done.update(
                        translated=True, reason="adopted the ticket's own translation"
                    )
                    return done
                chain = _ingress(ticket_id)
        except Exception:
            done["reason"] = "the ticket's chain could not be run"
            frappe.log_error(
                title="Helpdesk AI ingress",
                message=f"ingress via opening message {message} failed\n\n"
                f"{frappe.get_traceback()}",
            )
            return done
        done.update(
            translated=bool(chain.get("translated")),
            reason=(
                "translated with the ticket"
                if chain.get("translated")
                else "already readable"
            ),
        )
        return done

    try:
        # Since Wave 14 the engine detects the language inside this call — the
        # word-list gate that used to sit here filed most short or formal mail
        # as undetectable, which was the point of removing it. The result says
        # whether anything was recorded; "recorded": False is a verdict, not a
        # failure.
        result = translation.generate_inbound_translation(
            ticket_id=ticket_id,
            original_text=text,
            target_language=translation.get_working_language(),
            message=message,
            idempotency_key=_message_key(message),
        )
        if result.get("name"):
            done["translated"] = True
        else:
            done["reason"] = result.get("reason") or "already readable"
    except Exception:
        done["reason"] = "the provider did not answer"
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"translation failed for message {message}\n\n{frappe.get_traceback()}",
        )
    return done


def run_ingress(ticket_id: str) -> dict:
    """Detect, translate and triage one ticket. Runs in a worker, not a request.

    Under the ticket's lock, because the opening message's job runs the same
    chain when it is picked up first; see `translate_message`. A lock that
    cannot be had within the wait is logged and the chain runs anyway — every
    step replays on its key, so the worst case is the race this lock exists
    to close, not a ticket left unread.
    """
    try:
        with _ticket_lock(ticket_id):
            return _ingress(ticket_id)
    except Exception as exc:
        if not _is_lock_error(exc):
            raise
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"could not take the lock for {ticket_id}; running unlocked\n\n"
            f"{frappe.get_traceback()}",
        )
        return _ingress(ticket_id)


def _ingress(ticket_id: str) -> dict:
    """The chain itself: ticket text, then the opening message, then triage.

    Each step is attempted on its own: a translation that fails is not a reason
    to skip the triage, and neither is a reason to leave a mark on the ticket.
    """
    done = {"ticket": ticket_id, "translated": False, "triaged": False}
    if not ai_runner.is_runner_available():
        return done

    text = _ticket_text(ticket_id)
    working = translation.get_working_language()

    # Since Wave 14 the engine does the detecting, inside the translation call.
    # The word-list gate that stood here scored most short or formal mail as
    # undetectable in all six languages, and an undetected English ticket sat
    # untranslated looking exactly like one that needed nothing.
    if text.strip():
        try:
            # A message on the ticket may already carry these words — a chain
            # started by an agent after the reply job ran, say. A translation
            # that was already done is a reason to skip a call, not a reason
            # to skip the rest of the chain.
            if _covered_by_message(ticket_id, text):
                done["translated"] = True
            elif _already_readable(ticket_id):
                pass
            else:
                result = translation.generate_inbound_translation(
                    ticket_id=ticket_id,
                    original_text=text,
                    target_language=working,
                    idempotency_key=f"ingress-translate-{ticket_id}",
                )
                done["translated"] = bool(result.get("name"))
                if not done["translated"]:
                    _mark_readable(ticket_id, result.get("reason"))
            # Ticket, then message, in this order and in this job: the opening
            # message reads through the ticket's translation, and handing it
            # over here is what leaves nothing for its own job to buy.
            if done["translated"]:
                _hand_to_opening_message(ticket_id)
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


def _ticket_lock(ticket_id: str):
    """One holder at a time for a ticket's chain, across workers.

    A Redis lock rather than a file lock: a file per ticket would litter the
    site's lock directory for the life of the site, and the cache is where the
    queue already lives. It expires on its own, so a worker killed mid-call
    cannot hold a ticket's chain hostage.
    """
    return frappe.cache().lock(
        f"{JOB_PREFIX}-lock-{ticket_id}",
        timeout=LOCK_EXPIRY_SECONDS,
        blocking_timeout=LOCK_WAIT_SECONDS,
        # A chain that outlived its lock has still run once; releasing a lock
        # somebody else now holds is not a reason to run it again.
        raise_on_release_error=False,
    )


def _is_lock_error(exc: Exception) -> bool:
    from redis.exceptions import LockError

    return isinstance(exc, LockError)


def _readable_key(ticket_id: str) -> str:
    return f"{JOB_PREFIX}-readable-{ticket_id}"


def _already_readable(ticket_id: str) -> bool:
    """Whether the engine has already said the ticket needs no translation.

    That verdict records no row, so nothing replays it: the ticket's key finds
    nothing and the next run of the chain — the opening message's job, or an
    agent asking — would pay to hear it again. The verdict is remembered for a
    while in the cache instead. Remembered, not stored: a Swedish ticket is
    the ordinary case and not a fact anyone audits.
    """
    return bool(frappe.cache().get_value(_readable_key(ticket_id)))


def _mark_readable(ticket_id: str, reason: str | None) -> None:
    frappe.cache().set_value(
        _readable_key(ticket_id), reason or "already readable", expires_in_sec=24 * 3600
    )


def _message_key(message: str) -> str:
    """The idempotency key of a message's translation, generated or adopted."""
    return f"ingress-translate-message-{message}"


def _is_opening_message(message: str, ticket_id: str) -> bool:
    """Whether this is the first thing the customer wrote on the ticket.

    First by creation among the Received messages: the one Helpdesk made from
    the description when the ticket was opened, whose words the ticket's own
    translation already covers.
    """
    first = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "sent_or_received": "Received",
        },
        pluck="name",
        order_by="creation asc, name asc",
        limit=1,
    )
    return bool(first) and first[0] == message


def _hand_to_opening_message(ticket_id: str) -> str | None:
    """Give the ticket's translation to the message that carries the same words."""
    row = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "sent_or_received": "Received",
        },
        fields=["name", "content"],
        order_by="creation asc, name asc",
        limit=1,
    )
    if not row:
        return None
    text = strip_html(row[0].content or "").strip()
    return _adopt(ticket_id, row[0].name, text) if text else None


def _adopt(ticket_id: str, message: str, text: str) -> str | None:
    """Record, for a message, the ticket's translation of the same words.

    A row of its own rather than a stamp on the ticket's row, so the record
    says two things one field could not: the ticket's row is the one that was
    paid for, and the message's row wears it (`adopted_from`). Keyed on the
    message like a generated translation would be, so a job that runs twice
    replays rather than adopting twice.
    """
    existing = frappe.db.get_value(
        "HD Message Translation", {"idempotency_key": _message_key(message)}, "name"
    )
    if existing:
        return existing
    source = _adoptable(ticket_id, text)
    if not source:
        return None
    paid = frappe.db.get_value(
        "HD Message Translation",
        source,
        ["original_text", "translated_text", "source_language", "target_language",
         "provider", "model_version", "prompt_version"],
        as_dict=True,
    )
    recorded = translation.record_translation(
        ticket_id=ticket_id,
        original_text=text,
        translated_text=paid.translated_text,
        source_language=paid.source_language,
        target_language=paid.target_language,
        direction="Inbound",
        message=message,
        provider=paid.provider,
        model_version=paid.model_version,
        prompt_version=paid.prompt_version,
        idempotency_key=_message_key(message),
        adopted_from=source,
    )
    return recorded.get("name")


def _squashed(text: str) -> str:
    """Compare texts without letting whitespace decide the answer."""
    return " ".join((text or "").split())


def _adoptable(ticket_id: str, text: str) -> str | None:
    """The ticket's own translation of these very words, if it has one.

    Matched on the tail rather than on equality. What the ticket carries is
    `subject + description`, and what the message carries is the description
    alone — so the two differ by the subject line and an equality test called
    them different texts. That cost a second model call on every email-opened
    ticket, and left the band printing what the thread already showed. The
    live demo found it; a test now holds it.

    Only a row that was paid for is adoptable: one that belongs to no message
    and adopted nothing itself. A message's row is that message's, whatever
    it says.
    """
    wanted = _squashed(text)
    if not wanted:
        return None
    for row in frappe.get_all(
        "HD Message Translation",
        filters={"ticket": ticket_id, "direction": "Inbound"},
        fields=["name", "original_text", "message", "adopted_from"],
        order_by="creation asc",
    ):
        if row.message or row.adopted_from:
            continue
        if _squashed(row.original_text).endswith(wanted):
            return row.name
    return None


def _covered_by_message(ticket_id: str, text: str) -> bool:
    """Has a message on this ticket already been translated with these words?

    The mirror of `_adoptable`: there the ticket's translation is claimed by a
    message, here a message's translation is what makes the ticket's redundant.
    A row that adopted the ticket's translation does not count — it is the
    ticket's translation, and the ticket's key replays it anyway.
    """
    wanted = _squashed(text)
    if not wanted:
        return False
    return any(
        row.message and not row.adopted_from and wanted.endswith(_squashed(row.original_text))
        for row in frappe.get_all(
            "HD Message Translation",
            filters={"ticket": ticket_id, "direction": "Inbound"},
            fields=["original_text", "message", "adopted_from"],
        )
    )


def _ticket_text(ticket_id: str) -> str:
    """The text a model should read: what the customer actually wrote."""
    from helpdesk.api import ai_generation

    return ai_generation.ticket_text(ticket_id)
