import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class HDAIMCPServer(Document):
    """An MCP server a raphain runner spawns over stdio."""

    def validate(self):
        """Refuse a spawn configuration `raphain_mcp::SpawnOptions` could not take.

        Every save path runs this, so a server configured from the desk, from a
        fixture or from the API is held to the same shape.
        """
        self.validate_arguments()
        self.validate_timeouts()

    def validate_arguments(self):
        """Command arguments are a list of strings, the way a process takes them."""
        arguments = self.arguments
        if arguments in (None, ""):
            return
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                frappe.throw(_("Arguments must be valid JSON."))
        if not isinstance(arguments, list) or any(
            not isinstance(argument, str) for argument in arguments
        ):
            frappe.throw(_("Arguments must be a list of strings."))

    def validate_timeouts(self):
        """A handshake or call that may not take any time can never succeed."""
        for fieldname, label in (
            ("handshake_timeout", _("Handshake timeout")),
            ("call_timeout", _("Call timeout")),
        ):
            if cint(self.get(fieldname)) <= 0:
                frappe.throw(_("{0} must be a positive number of seconds.").format(label))
