import frappe

from helpdesk.utils import agent_only

SUBMISSION_DOCTYPE = "HD External Order Submission"


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
