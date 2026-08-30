"""Helpdesk-side configuration of the MCP servers a raphain runner spawns.

Helpdesk owns the configuration; `raphain-mcp` v0.1 speaks stdio only, so a
server here is a command, its arguments and the handshake and call timeouts.
"""

import frappe

from helpdesk.utils import agent_only

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
