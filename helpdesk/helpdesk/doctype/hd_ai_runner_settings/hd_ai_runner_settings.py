import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class HDAIRunnerSettings(Document):
    """How Helpdesk reaches the raphain-embedding runner that generates text."""

    def validate(self):
        """Refuse a configuration no request could ever be made with."""
        self.validate_request_timeout()
        self.validate_runner_url()

    def validate_request_timeout(self):
        """A request that may not take any time can never come back with text."""
        if cint(self.request_timeout) <= 0:
            frappe.throw(_("Request timeout must be a positive number of seconds."))

    def validate_runner_url(self):
        """Switching the runner on means naming the service to call."""
        if cint(self.enabled) and not self.runner_url:
            frappe.throw(_("An enabled AI runner needs a runner URL."))
