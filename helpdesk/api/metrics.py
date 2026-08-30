import frappe
from frappe.utils import flt

from helpdesk.utils import agent_only

SUBMISSION_DOCTYPE = "HD External Order Submission"
TRIAGE_DOCTYPE = "HD AI Triage Result"
REPLY_DRAFT_DOCTYPE = "HD AI Reply Draft"
REPLY_STATUSES = ("Draft", "Pending Approval", "Approved", "Sent", "Rejected")
ACCEPTED_REPLY_STATUSES = ("Approved", "Sent")


def _submission_counts():
    """Count external order submissions by origin, zero when none were ever made."""
    if not frappe.db.exists("DocType", SUBMISSION_DOCTYPE):
        return 0, 0
    automated = frappe.db.count(SUBMISSION_DOCTYPE, {"automated": 1})
    manual = frappe.db.count(SUBMISSION_DOCTYPE, {"automated": 0})
    return automated, manual


@frappe.whitelist()
@agent_only
def automation_metrics():
    """Report how many orders reached the external system without a person."""
    automated, manual = _submission_counts()
    return {"automated_submissions": automated, "manual_submissions": manual}


@frappe.whitelist()
@agent_only
def classification_accuracy():
    """Report how often an agent had to correct the AI's classification."""
    total = frappe.db.count(TRIAGE_DOCTYPE)
    corrected = frappe.db.count(TRIAGE_DOCTYPE, {"status": "Corrected"})
    accuracy = 1 - (corrected / total) if total else 0
    return {"total": total, "corrected": corrected, "accuracy": flt(accuracy, 4)}


@frappe.whitelist()
@agent_only
def reply_approval_metrics():
    """Report how the replies the AI drafted were received by their reviewers."""
    metrics = {}
    total = 0
    accepted = 0
    for status in REPLY_STATUSES:
        count = frappe.db.count(REPLY_DRAFT_DOCTYPE, {"status": status})
        metrics[status.lower().replace(" ", "_")] = count
        total += count
        if status in ACCEPTED_REPLY_STATUSES:
            accepted += count
    metrics["total"] = total
    metrics["approval_rate"] = flt(accepted / total, 4) if total else 0
    return metrics
