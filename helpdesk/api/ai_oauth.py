"""Where an AI engine's OAuth token comes from.

Wave 6 could store an access token but never obtain one, which left an
administrator with no API key nothing to type. This module describes the
identity providers a grant can be obtained from, so the rest of the flow has an
address, a client and an honest set of capabilities to work from.

The capabilities are the point. OpenAI publishes no dynamic client registration
and no device authorization endpoint, and the public Codex client's registered
redirect is a loopback URI on the administrator's own machine — so "redirect
back to Frappe" is not a mode that exists there, however much we would like it
to be. A provider offers the modes it actually has, and no others.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from helpdesk.helpdesk.doctype.hd_ai_oauth_provider.hd_ai_oauth_provider import refuse
from helpdesk.utils import agent_only, is_admin

# raphain's src/auth.rs and book/src/ch26-codex-backend.md settle the two vendor
# presets. Neither can be discovered: OpenAI's document advertises no device
# flow and no registration endpoint, and the wire quirks below are the ones
# raphain's declarative RegistryConfig cannot express for itself.
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# The Anthropic API reads system[0] and answers 429 on the first request of a
# session whose first system block is not this exact identifier.
CLAUDE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."

DEFAULT_PRESET = "generic_oidc"

PRESETS = {
    "openai_codex": {
        "preset": "openai_codex",
        "client_id": CODEX_CLIENT_ID,
        "issuer": "https://auth.openai.com",
        "authorization_endpoint": "https://auth.openai.com/oauth/authorize",
        "token_endpoint": "https://auth.openai.com/oauth/token",
        # RFC 8252: the registered redirect is on the administrator's machine, so
        # no server-side callback can ever receive it. Hence paste-back only.
        "redirect_uri": "http://localhost:1455/auth/callback",
        # raphain's openai_codex() asks for no scope, and asking for one narrows
        # what the token can do rather than widening it.
        "scope": "",
        "token_body_encoding": "json",
        "supports_pkce": 1,
        "supports_loopback_paste": 1,
        "supports_redirect": 0,
        "supports_device_code": 0,
        # Without these the minted token carries no chatgpt_account_id claim and
        # every backend call fails one step later on a missing header.
        "extra_authorize_params": {
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli_rs",
        },
        # The ChatGPT bearer is not an API key: it belongs to the Codex backend
        # and must never reach api.openai.com.
        "engine_base_url": "https://chatgpt.com/backend-api/codex",
        "engine_headers": [{"name": "originator", "value": "codex_cli_rs"}],
        "engine_options": {"force_stream": True},
        "engine_parameters": {"extra": {"store": False}},
    },
    "anthropic_claude": {
        "preset": "anthropic_claude",
        "client_id": CLAUDE_CLIENT_ID,
        "issuer": "https://claude.ai",
        "authorization_endpoint": "https://claude.ai/oauth/authorize",
        "token_endpoint": "https://console.anthropic.com/v1/oauth/token",
        # Paste-back too, but off a hosted callback: raphain notes that loopback
        # URIs are not in Anthropic's allowlist for this client.
        "redirect_uri": "https://platform.claude.com/oauth/code/callback",
        # A broader set often yields a token whose granted scope omits this one.
        "scope": "user:inference",
        "token_body_encoding": "json",
        "supports_pkce": 1,
        "supports_loopback_paste": 1,
        "supports_redirect": 0,
        "supports_device_code": 0,
        # An Anthropic extension: the state is echoed in the exchange body as
        # well as in the authorize URL.
        "state_in_token_body": 1,
        "extra_token_headers": {"anthropic-beta": "oauth-2025-04-20"},
        "engine_options": {"system_prefix_block": CLAUDE_SYSTEM_PREFIX},
    },
    DEFAULT_PRESET: {
        "preset": DEFAULT_PRESET,
        # RFC 6749 says form; only the two vendor presets disagree.
        "token_body_encoding": "form",
        "supports_pkce": 1,
        "supports_redirect": 1,
    },
}

PROVIDER_FIELDS = [
    "provider_name",
    "preset",
    "client_id",
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "device_authorization_endpoint",
    "revocation_endpoint",
    "redirect_uri",
    "scope",
    "token_body_encoding",
    "supports_pkce",
    "supports_redirect",
    "supports_loopback_paste",
    "supports_device_code",
    "enabled",
]

FLAG_FIELDS = (
    "supports_pkce",
    "supports_redirect",
    "supports_loopback_paste",
    "supports_device_code",
    "state_in_token_body",
    "allow_insecure_loopback",
    "enabled",
)

DOCUMENT_FIELDS = (
    "extra_authorize_params",
    "extra_token_headers",
    "engine_headers",
    "engine_options",
    "engine_parameters",
)


def _require_admin() -> None:
    """Binding an external identity to the shared engine is not an agent's act.

    The inference the whole helpdesk runs would otherwise be billed to, and
    logged against, one person's personal ChatGPT or Claude account.
    """
    if not is_admin():
        refuse(
            "not_permitted",
            _("Only an administrator may configure AI engine authorization."),
            frappe.PermissionError,
        )


def _as_document_text(value: dict | list | str) -> str:
    """A JSON docfield holds text, whether the caller sent data or JSON already."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


@frappe.whitelist(methods=["POST"])
def upsert_provider(
    provider_name: str,
    preset: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    issuer: str | None = None,
    discovery_url: str | None = None,
    authorization_endpoint: str | None = None,
    token_endpoint: str | None = None,
    device_authorization_endpoint: str | None = None,
    revocation_endpoint: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    token_body_encoding: str | None = None,
    supports_pkce: int | bool | None = None,
    supports_redirect: int | bool | None = None,
    supports_loopback_paste: int | bool | None = None,
    supports_device_code: int | bool | None = None,
    state_in_token_body: int | bool | None = None,
    allow_insecure_loopback: int | bool | None = None,
    extra_authorize_params: dict | list | str | None = None,
    extra_token_headers: dict | list | str | None = None,
    engine_base_url: str | None = None,
    engine_headers: dict | list | str | None = None,
    engine_options: dict | list | str | None = None,
    engine_parameters: dict | list | str | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Create or update one OAuth provider, and return it as configured.

    A new record starts from its preset, so an administrator naming
    `openai_codex` gets the public Codex client and its loopback redirect
    without retyping either. An existing one is edited field by field: the
    preset is not re-applied over an administrator's own values.
    """
    _require_admin()
    if frappe.db.exists("HD AI OAuth Provider", provider_name):
        doc = frappe.get_doc("HD AI OAuth Provider", provider_name)
    else:
        doc = frappe.new_doc("HD AI OAuth Provider")
        doc.provider_name = provider_name
        doc.update(PRESETS.get(preset or DEFAULT_PRESET, {}))
    given = {
        "preset": preset,
        "client_id": client_id,
        "client_secret": client_secret,
        "issuer": issuer,
        "discovery_url": discovery_url,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "device_authorization_endpoint": device_authorization_endpoint,
        "revocation_endpoint": revocation_endpoint,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "token_body_encoding": token_body_encoding,
        "engine_base_url": engine_base_url,
        "supports_pkce": supports_pkce,
        "supports_redirect": supports_redirect,
        "supports_loopback_paste": supports_loopback_paste,
        "supports_device_code": supports_device_code,
        "state_in_token_body": state_in_token_body,
        "allow_insecure_loopback": allow_insecure_loopback,
        "enabled": enabled,
        "extra_authorize_params": extra_authorize_params,
        "extra_token_headers": extra_token_headers,
        "engine_headers": engine_headers,
        "engine_options": engine_options,
        "engine_parameters": engine_parameters,
    }
    for fieldname, value in given.items():
        if value is None:
            continue
        if fieldname in FLAG_FIELDS:
            doc.set(fieldname, cint(value))
        elif fieldname in DOCUMENT_FIELDS:
            doc.set(fieldname, _as_document_text(value))
        else:
            doc.set(fieldname, value)
    doc.save(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def list_providers() -> list:
    """Return the providers an engine can be bound to, newest first.

    The client secret is not among the fields: nothing that reads this list has
    any use for it.
    """
    return frappe.get_all(
        "HD AI OAuth Provider",
        filters={"enabled": 1},
        fields=PROVIDER_FIELDS,
        order_by="creation desc",
    )
