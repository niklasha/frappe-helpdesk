"""Helpdesk-side configuration of the MCP servers a raphain runner spawns.

Helpdesk owns the configuration; `raphain-mcp` v0.1 speaks stdio only, so a
server here is a command, its arguments and the handshake and call timeouts.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin

SERVER_FIELDS = [
    "name",
    "server_name",
    "command",
    "arguments",
    "protocol_version",
    "handshake_timeout",
    "call_timeout",
    "enabled",
]


@frappe.whitelist()
@agent_only
def list_mcp_servers() -> list:
    """Return the MCP servers Helpdesk is configured to dispatch to, newest first."""
    return frappe.get_all(
        "HD AI MCP Server",
        filters={"enabled": 1},
        fields=SERVER_FIELDS,
        order_by="creation desc",
    )


def require_mcp_admin() -> None:
    """Deciding which processes the helpdesk may spawn is an administrative act."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator can configure MCP servers."),
            frappe.PermissionError,
        )


def _as_json(value):
    """A JSON docfield takes text, whether the caller sent a list or a document."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value)


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_mcp_server(
    server_name: str,
    command: str | None = None,
    arguments: dict | list | str | None = None,
    protocol_version: str | None = None,
    handshake_timeout: int | None = None,
    call_timeout: int | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Create or update the stdio configuration of one MCP server."""
    require_mcp_admin()
    if frappe.db.exists("HD AI MCP Server", server_name):
        doc = frappe.get_doc("HD AI MCP Server", server_name)
    else:
        doc = frappe.new_doc("HD AI MCP Server")
        doc.server_name = server_name
    if command is not None:
        doc.command = command
    if arguments is not None:
        doc.arguments = _as_json(arguments)
    if protocol_version is not None:
        doc.protocol_version = protocol_version
    if handshake_timeout is not None:
        doc.handshake_timeout = cint(handshake_timeout)
    if call_timeout is not None:
        doc.call_timeout = cint(call_timeout)
    if enabled is not None:
        doc.enabled = cint(enabled)
    doc.save(ignore_permissions=True)
    return doc.as_dict()
