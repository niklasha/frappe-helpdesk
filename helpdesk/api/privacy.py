import frappe
from frappe import _
from frappe.utils import add_days, cint, now_datetime

from helpdesk.utils import agent_only, is_admin


def require_privacy_admin():
    """Privacy administration is reserved for administrators."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator may administer privacy settings."),
            frappe.PermissionError,
        )


@frappe.whitelist(methods=["POST"])
@agent_only
def set_retention_policy(reference_doctype: str, retain_days: int) -> dict:
    """Configure for how many days documents of one doctype are kept."""
    require_privacy_admin()
    existing = frappe.db.exists("HD Retention Policy", reference_doctype)
    doc = (
        frappe.get_doc("HD Retention Policy", existing)
        if existing
        else frappe.get_doc(
            {"doctype": "HD Retention Policy", "reference_doctype": reference_doctype}
        )
    )
    doc.retain_days = cint(retain_days)
    doc.enabled = 1
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def retention_policies() -> list:
    """Return the retention periods currently in force."""
    return frappe.get_all(
        "HD Retention Policy",
        filters={"enabled": 1},
        fields=["name", "reference_doctype", "retain_days", "enabled"],
        order_by="reference_doctype asc",
    )


@frappe.whitelist()
@agent_only
def expired_documents(reference_doctype: str) -> list:
    """Report the documents that have outlived their retention period.

    Reporting only: Helpdesk names what has expired and deletes nothing, so
    removal stays a deliberate act.
    """
    require_privacy_admin()
    policy = frappe.db.get_value(
        "HD Retention Policy",
        {"reference_doctype": reference_doctype, "enabled": 1},
        ["retain_days"],
        as_dict=True,
    )
    if not policy:
        return []
    cutoff = add_days(now_datetime(), -cint(policy.retain_days))
    return frappe.get_all(
        reference_doctype,
        filters={"creation": ["<", cutoff]},
        pluck="name",
        order_by="creation asc",
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def anonymize_contact(email: str) -> int:
    """Replace a contact's address on their tickets with an irreversible placeholder.

    The placeholder carries a random hash, so the original address cannot be
    derived from it and the erasure cannot be undone.
    """
    require_privacy_admin()
    if not email:
        return 0
    tickets = frappe.get_all("HD Ticket", filters={"raised_by": email}, pluck="name")
    if not tickets:
        return 0
    placeholder = f"anonymized-{frappe.generate_hash(length=12)}@example.invalid"
    for ticket in tickets:
        frappe.db.set_value("HD Ticket", ticket, "raised_by", placeholder)
    frappe.logger("helpdesk").info(
        f"Anonymized {len(tickets)} tickets as {placeholder} by {frappe.session.user}"
    )
    return len(tickets)


DECLARATION_FIELDS = ("purpose", "data_categories", "agreement_reference", "hosting_region")


@frappe.whitelist(methods=["POST"])
@agent_only
def declare_ai_processing(provider: str, **values) -> dict:
    """Record how an external AI provider processes personal data for us."""
    existing = frappe.db.exists("HD AI Processing Declaration", provider)
    doc = (
        frappe.get_doc("HD AI Processing Declaration", existing)
        if existing
        else frappe.get_doc(
            {"doctype": "HD AI Processing Declaration", "provider": provider}
        )
    )
    for field in DECLARATION_FIELDS:
        if values.get(field) is not None:
            doc.set(field, values[field])
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def ai_processing_declarations() -> list:
    """Return every declared external AI processing arrangement."""
    return frappe.get_all(
        "HD AI Processing Declaration",
        fields=["name", "provider", *DECLARATION_FIELDS],
        order_by="provider asc",
    )


def has_ai_training_consent(customer):
    """Whether a customer has explicitly consented to training use of their data."""
    if not customer:
        return False
    return bool(
        cint(frappe.db.get_value("HD Customer", customer, "ai_training_consent"))
    )


@frappe.whitelist()
@agent_only
def ai_training_allowed(customer: str) -> bool:
    """Report the consent decision as a boolean.

    Silence must never be read as permission, so the answer is a real boolean:
    a caller that treats the response as a truth value reads a refusal as a
    refusal, which a string such as "false" would not convey.
    """
    return has_ai_training_consent(customer)


@frappe.whitelist(methods=["POST"])
@agent_only
def set_ai_training_consent(customer: str, consent: int | bool) -> dict:
    """Record a customer's decision about training use, and log who took it."""
    frappe.has_permission("HD Customer", "write", doc=customer, throw=True)
    doc = frappe.get_doc("HD Customer", customer)
    doc.ai_training_consent = cint(consent)
    doc.save(ignore_permissions=True)
    frappe.logger("helpdesk").info(
        f"AI training consent for {customer} set to {doc.ai_training_consent} by {frappe.session.user}"
    )
    return doc.as_dict()
