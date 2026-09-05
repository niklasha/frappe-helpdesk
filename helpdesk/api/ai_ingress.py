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

from helpdesk.api import ai_runner, ai_triage, order_extraction, translation
from helpdesk.helpdesk.doctype.hd_ticket_type.hd_ticket_type import classification_group
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
# How long a *request* waits for the same lock. An agent's click must not hang
# a web request for two minutes behind a worker's model call; a request that
# finds the ticket busy says so and lets the worker's result land on its own.
REQUEST_LOCK_WAIT_SECONDS = 3
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
    opening = _is_opening_message(doc.reference_name, strip_html(doc.content or ""))
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

    # Wave 17 (AIAN-17): a customer reply is read against the verdict already
    # standing. Only when one stands: the opening mail's own Communication
    # arrives right behind the ticket, before the ticket job has run, and the
    # ticket job covers that text. Queueing a second triage for it would buy the
    # same reading twice and record it twice.
    if not frappe.db.exists("HD AI Triage Result", {"ticket": doc.reference_name}):
        return
    try:
        frappe.enqueue(
            "helpdesk.api.ai_ingress.retriage_for_message",
            queue="short",
            job_id=f"{JOB_PREFIX}-retriage-{doc.name}",
            deduplicate=True,
            message=doc.name,
            enqueue_after_commit=True,
        )
    except Exception:
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"could not queue re-triage for {doc.name}\n\n{frappe.get_traceback()}",
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def translate_message(message: str) -> dict:
    """Put one customer message into the working language, beside its original.

    Runs in a worker for the queued path and inline for an agent who asks. Both
    are safe to repeat: the idempotency key is the message, so a retry replays
    the first answer rather than buying a second.

    For the ticket's opening message this prefers not to generate on its own.
    Those words are the ticket's description, and the ticket's chain translates
    them once and hands the result to this message. If that chain has not run
    yet when this job is picked up, the job takes the ticket's lock and runs
    the chain itself — every step of it is keyed, so the ticket's own job then
    finds everything replayed and pays nothing. Waiting for the other job
    instead would deadlock a single worker, which is what this bench runs:
    the job in front of the queue would be waiting for the job behind it.

    "Opening" is decided by content, not by position: the message is the
    opening one when the ticket's description ends with its words. The first
    Received message on a ticket opened without a description is a real reply
    and is translated like any other. And should the chain come back without
    a row for this message — it judged the ticket readable, the provider did
    not answer, the description changed since — the message is translated the
    ordinary way rather than left with nothing.

    Inline, for an agent, this waits only briefly for a lock a worker holds:
    a click must not hang behind a model call. A busy ticket answers with
    `"running": True`, and the worker's result lands on its own.
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

    if _is_opening_message(ticket_id, text):
        try:
            with _ticket_lock(ticket_id, _lock_wait()):
                # Under the lock: a chain that finished while we waited for it
                # has left a row to adopt, and one that never ran is run here.
                if _adopt(ticket_id, message, text):
                    done.update(
                        translated=True, reason="adopted the ticket's own translation"
                    )
                    return done
                _ingress(ticket_id)
        except Exception as exc:
            if _is_lock_error(exc) and frappe.request:
                done.update(running=True, reason="the ticket's chain is running")
                return done
            done["reason"] = "the ticket's chain could not be run"
            frappe.log_error(
                title="Helpdesk AI ingress",
                message=f"ingress via opening message {message} failed\n\n"
                f"{frappe.get_traceback()}",
            )
            return done
        if _recorded_for(message):
            done.update(translated=True, reason="translated with the ticket")
            return done
        if _already_readable(ticket_id):
            # The verdict was on the description, which holds these very words.
            done["reason"] = "already readable"
            return done
        # The chain ran and left this message nothing — its translation failed,
        # or the words no longer match. The ordinary path below is keyed on the
        # message and is the honest fallback.

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


def retriage_for_message(message: str) -> dict:
    """Read one customer reply against the ticket's standing verdict. Worker only.

    The demo finding: a quote request arrived without a quantity, the model said
    so, the customer answered with the number, and the panel went on saying the
    quantity was missing. The chain ran once, keyed on the ticket. This is the
    second run: the earlier verdict, the ticket text and the reply are put in
    front of the model together, and the answer is a new row beside the old one
    — never over it, because what the model proposed and when is audit.

    Keyed on the message, so a retry replays rather than pays, and one reply is
    one re-triage however many times the job is queued.
    """
    done = {"message": message, "triaged": False, "reason": None}
    row = frappe.db.get_value(
        "Communication",
        message,
        ["reference_doctype", "reference_name", "sent_or_received"],
        as_dict=True,
    )
    if not row or row.reference_doctype != "HD Ticket" or not row.reference_name:
        done["reason"] = "not a ticket message"
        return done
    if row.sent_or_received != "Received":
        done["reason"] = "not from the customer"
        return done
    if not ai_runner.is_runner_available():
        done["reason"] = "no runner"
        return done
    previous = ai_triage.latest_triage(row.reference_name)
    if not previous:
        # Checked again here: the enqueue hook looked, but the ticket job may
        # have failed since, and a re-triage with nothing to re-read is the
        # first triage under the wrong key.
        done["reason"] = "the ticket has no triage to revisit"
        return done
    try:
        ai_triage.triage_ticket(
            ticket_id=row.reference_name,
            idempotency_key=f"ingress-retriage-{message}",
            source_message=message,
            previous=previous,
        )
        done["triaged"] = True
    except Exception:
        done["reason"] = "the provider did not answer"
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"re-triage failed for message {message}\n\n{frappe.get_traceback()}",
        )
        return done
    # Wave 18 (ORDR-05): the card follows the conversation the way the verdict
    # does. Keyed on the message, so the reply that changed the quantity gets a
    # row of its own beside the opening mail's, and a re-run replays it.
    done["extracted"] = _extract_if_order(
        row.reference_name,
        idempotency_key=f"ingress-extract-{row.reference_name}-{message}",
        source_message=message,
    )
    return done


def run_ingress(ticket_id: str, wait: int = LOCK_WAIT_SECONDS) -> dict:
    """Detect, translate and triage one ticket. The queued job's entry point.

    Under the ticket's lock, because the opening message's job runs the same
    chain when it is picked up first; see `translate_message`. A lock that
    cannot be had within the wait is logged and the chain runs anyway — every
    step replays on its key, so the worst case is the race this lock exists
    to close, not a ticket left unread.

    `wait` is how long to sit on that lock; the job waits out a model call,
    a request (`triage_incoming_ticket`) does not — see `_run_now`.
    """
    try:
        with _ticket_lock(ticket_id, wait):
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


def _run_now(ticket_id: str) -> dict:
    """The chain for a request: a short wait on the lock, then an honest answer.

    A worker holding the ticket's lock is in the middle of a model call, and
    the agent who clicked must neither wait for it nor start a second one.
    The reply says the chain is running and what the ticket has so far.
    """
    try:
        with _ticket_lock(ticket_id, REQUEST_LOCK_WAIT_SECONDS):
            return _ingress(ticket_id)
    except Exception as exc:
        if not _is_lock_error(exc):
            raise
        return {"ticket": ticket_id, "running": True, **_state(ticket_id)}


def _state(ticket_id: str) -> dict:
    """What the chain has already left on a ticket, for a reply that ran nothing."""
    return {
        "translated": bool(
            frappe.db.exists(
                "HD Message Translation", {"ticket": ticket_id, "direction": "Inbound"}
            )
        ),
        "triaged": bool(
            frappe.db.exists(
                "HD AI Triage Result", {"idempotency_key": f"ingress-triage-{ticket_id}"}
            )
        ),
    }


def _lock_wait() -> int:
    """How long the caller may sit on a ticket's lock: a job waits, a request does not."""
    return REQUEST_LOCK_WAIT_SECONDS if frappe.request else LOCK_WAIT_SECONDS


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

    # Wave 18 (ORDR-05): the triage said Order; that is the signal to read the
    # order details too, so the card in the sidebar has something to show
    # without anyone clicking. Keyed on the ticket like the triage, and only
    # ever an extraction — no submission, no order, until a person says so.
    done["extracted"] = _extract_if_order(
        ticket_id, idempotency_key=f"ingress-extract-{ticket_id}"
    )
    return done


def _is_order(ticket_id: str) -> bool:
    """Whether the ticket's standing verdict files it as an order.

    The catalogued type's group is the gate — the vocabulary wave put it on
    `HD Ticket Type` precisely so a chain could ask this without a second
    list of labels. The newest proposal is read, corrected type first, and
    when its type names a group, that group decides: a triage that says
    Reklamation is not overruled by a keyword class that said Order before the
    model got to it. The ticket's own coarse class counts only when there
    is no triage, or the type states no group — a ticket an agent typed by
    hand, or one filed under a group-less type.
    """
    triage = ai_triage.latest_triage(ticket_id)
    if triage:
        types = frappe.db.get_value(
            "HD AI Triage Result",
            triage,
            ["corrected_ticket_type", "proposed_ticket_type"],
            as_dict=True,
        ) or {}
        ticket_type = types.get("corrected_ticket_type") or types.get("proposed_ticket_type")
        group = classification_group(ticket_type)
        if group:
            return group == "Order"
    return frappe.db.get_value("HD Ticket", ticket_id, "classification_model") == "Order"


def _extract_if_order(
    ticket_id: str, idempotency_key: str, source_message: str | None = None
) -> bool:
    """Record the order details when the ticket is an order; otherwise nothing.

    An extraction that fails is a log line and a missing card, never a failed
    chain: the triage before it stands, and the ticket was never at risk.
    """
    try:
        if not _is_order(ticket_id):
            return False
        order_extraction.extract_order(
            ticket_id=ticket_id,
            idempotency_key=idempotency_key,
            source_message=source_message,
        )
        return True
    except Exception:
        frappe.log_error(
            title="Helpdesk AI ingress",
            message=f"order extraction failed for {ticket_id}\n\n{frappe.get_traceback()}",
        )
        return False


@frappe.whitelist(methods=["POST"])
@agent_only
def triage_incoming_ticket(ticket_id: str) -> dict:
    """Run the ingress chain for one ticket now, for an agent who asks.

    The same idempotency keys apply, so asking twice is free and asking after the
    automation already ran returns what it produced. Asking while the automation
    is running it returns `"running": True` and what there is so far, rather than
    holding the request until the worker's model call is over.
    """
    return _run_now(ticket_id)


def _ticket_lock(ticket_id: str, wait: int):
    """One holder at a time for a ticket's chain, across workers.

    A Redis lock rather than a file lock: a file per ticket would litter the
    site's lock directory for the life of the site, and the cache is where the
    queue already lives. It expires on its own, so a worker killed mid-call
    cannot hold a ticket's chain hostage. `wait` is how long acquiring may
    block before the lock raises instead.
    """
    return frappe.cache().lock(
        f"{JOB_PREFIX}-lock-{ticket_id}",
        timeout=LOCK_EXPIRY_SECONDS,
        blocking_timeout=wait,
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


def _recorded_for(message: str) -> bool:
    """Whether a translation row — generated or adopted — exists for this message."""
    return bool(
        frappe.db.exists("HD Message Translation", {"idempotency_key": _message_key(message)})
    )


def _is_opening_message(ticket_id: str, text: str) -> bool:
    """Whether a message's words are the ones the ticket was opened with.

    Decided by content: the ticket's description ends with the message's text
    (whitespace aside). Helpdesk makes the opening Communication from the
    description, so the two carry the same words — and the ticket's own
    translation already covers them. Position is not enough: a ticket opened
    without a description has no such mirror, and its first Received message
    is the customer's first real reply, which must be translated on its own.
    """
    wanted = _squashed(text)
    if not wanted:
        return False
    description = frappe.db.get_value("HD Ticket", ticket_id, "description")
    return _squashed(strip_html(description or "")).endswith(wanted)


def _hand_to_opening_message(ticket_id: str) -> str | None:
    """Give the ticket's translation to the message that carries the same words."""
    for row in frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "HD Ticket",
            "reference_name": ticket_id,
            "sent_or_received": "Received",
        },
        fields=["name", "content"],
        order_by="creation asc, name asc",
    ):
        text = strip_html(row.content or "").strip()
        if _is_opening_message(ticket_id, text):
            return _adopt(ticket_id, row.name, text)
    return None


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
