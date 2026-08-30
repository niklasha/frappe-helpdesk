"""Helpdesk's half of the raphain contract.

raphain is a Rust library crate: it has no service a Python process can call.
What it publishes is a configuration contract, so this module owns the part
Helpdesk can own honestly — describing the engines, and emitting the documents
a raphain-embedding runner consumes. No inference happens here.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin

ENGINE_FIELDS = ["engine_name", "kind", "model", "base_url", "is_default"]


def _require_admin() -> None:
    """Choosing which model the helpdesk talks to is an administrative act."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator may configure AI engines."),
            frappe.PermissionError,
        )


def _as_document_text(value: dict | list | str) -> str:
    """A JSON docfield holds text, whether the caller sent data or JSON already."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


@frappe.whitelist()
@agent_only
def list_engines() -> list:
    """Return the AI engines Helpdesk is configured to describe, newest first."""
    return frappe.get_all(
        "HD AI Engine",
        filters={"enabled": 1},
        fields=ENGINE_FIELDS,
        order_by="creation desc",
    )


@frappe.whitelist(methods=["POST"])
@agent_only
def upsert_engine(
    engine_name: str,
    kind: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    auth_type: str | None = None,
    auth_env: str | None = None,
    auth_secret: str | None = None,
    auth_header: str | None = None,
    auth_access_token_env: str | None = None,
    auth_access_token: str | None = None,
    auth_refresh_token_env: str | None = None,
    auth_expires_at_unix: int | None = None,
    auth_refresh: dict | list | str | None = None,
    parameters: dict | list | str | None = None,
    headers: dict | list | str | None = None,
    options: dict | list | str | None = None,
    pricing: dict | list | str | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Create or update one AI engine, and return it as configured."""
    _require_admin()
    if frappe.db.exists("HD AI Engine", engine_name):
        doc = frappe.get_doc("HD AI Engine", engine_name)
    else:
        doc = frappe.new_doc("HD AI Engine")
        doc.engine_name = engine_name
    if kind is not None:
        doc.kind = kind
    if model is not None:
        doc.model = model
    if base_url is not None:
        doc.base_url = base_url
    if auth_type is not None:
        doc.auth_type = auth_type
    if auth_env is not None:
        doc.auth_env = auth_env
    if auth_secret is not None:
        doc.auth_secret = auth_secret
    if auth_header is not None:
        doc.auth_header = auth_header
    if auth_access_token_env is not None:
        doc.auth_access_token_env = auth_access_token_env
    if auth_access_token is not None:
        doc.auth_access_token = auth_access_token
    if auth_refresh_token_env is not None:
        doc.auth_refresh_token_env = auth_refresh_token_env
    if auth_expires_at_unix is not None:
        doc.auth_expires_at_unix = cint(auth_expires_at_unix)
    if auth_refresh is not None:
        doc.auth_refresh = _as_document_text(auth_refresh)
    if parameters is not None:
        doc.parameters = _as_document_text(parameters)
    if headers is not None:
        doc.headers = _as_document_text(headers)
    if options is not None:
        doc.options = _as_document_text(options)
    if pricing is not None:
        doc.pricing = _as_document_text(pricing)
    if enabled is not None:
        doc.enabled = cint(enabled)
    doc.save(ignore_permissions=True)
    return doc.as_dict()
