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
from urllib.parse import urlsplit, urlunsplit

import frappe
import requests
from frappe import _
from frappe.utils import cint

from helpdesk.helpdesk.doctype.hd_ai_oauth_provider.hd_ai_oauth_provider import (
    check_endpoint,
    refuse,
)
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

# RFC 8628's grant, which a discovery document advertises alongside the endpoint.
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

# OpenID Connect Discovery 1.0 section 4: the issuer plus this path.
DISCOVERY_PATH = "/.well-known/openid-configuration"

# Every outbound call this module makes waits this long and no longer. Without a
# bound the worker is held for as long as the far end feels like holding the
# socket, and the unattended refresh then wedges a background worker per tick.
PROVIDER_TIMEOUT = 20

# The endpoints a discovery document may name, and the flag each one feeds.
DISCOVERED_ENDPOINTS = (
    "authorization_endpoint",
    "token_endpoint",
    "device_authorization_endpoint",
    "revocation_endpoint",
)

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


def call_provider(method: str, url: str, **options: object) -> tuple[int, dict]:
    """Make the one kind of outbound call this module makes, and always time it out.

    Discovery, the code exchange, the device poll and the refresh all reach a URL
    an administrator typed, so all of them go through here and all of them carry
    `timeout`. The status is handed back rather than judged: a token endpoint's
    400 carries the error the caller has to read, while a body that is not JSON
    carries nothing at all.
    """
    options.setdefault("timeout", PROVIDER_TIMEOUT)
    try:
        response = requests.request(method, url, **options)
    except requests.RequestException:
        # The exception text can repeat the body that was posted, which for a
        # refresh is a live credential. Only the address goes in the message.
        refuse("provider_unreachable", _("{0} did not answer in time.").format(url))
    try:
        answer = response.json()
    except ValueError:
        answer = {}
    return response.status_code, answer if isinstance(answer, dict) else {}


def _discovery_document_url(discovery_url: str) -> str:
    """Accept either the issuer or the document, and return the document's URL.

    An administrator is given an issuer — that is what a provider's own
    documentation prints — so the well-known path is appended for them. One who
    pastes the full document URL is left alone.
    """
    parts = urlsplit((discovery_url or "").strip())
    if "/.well-known/" in parts.path:
        return urlunsplit(parts._replace(query="", fragment=""))
    path = parts.path.rstrip("/") + DISCOVERY_PATH
    return urlunsplit(parts._replace(path=path, query="", fragment=""))


def _issuer_of(document_url: str) -> str:
    """The issuer the document at this URL is allowed to claim to be."""
    parts = urlsplit(document_url)
    path = parts.path[: -len(DISCOVERY_PATH)] if parts.path.endswith(DISCOVERY_PATH) else parts.path
    return urlunsplit(parts._replace(path=path.rstrip("/"), query="", fragment=""))


def _same_issuer(claimed: str, expected: str) -> bool:
    """Compare two issuers the way RFC 8414 does: on the authority, not the text."""
    left, right = urlsplit(claimed or ""), urlsplit(expected or "")
    return (
        left.scheme.lower() == right.scheme.lower()
        and (left.netloc or "").lower() == (right.netloc or "").lower()
        and left.path.rstrip("/") == right.path.rstrip("/")
    )


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


@frappe.whitelist(methods=["POST"])
def discover_provider(
    provider_name: str,
    discovery_url: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    scope: str | None = None,
    redirect_uri: str | None = None,
    token_body_encoding: str | None = None,
    allow_insecure_loopback: int | bool | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Configure a provider from the document it publishes about itself.

    An administrator pastes one URL and gets endpoints and capabilities that
    match what the provider actually offers, rather than four fields typed from
    a vendor page and a device flow that turns out not to exist. The flags are
    derived from the advertised grants and endpoints, never assumed: a mode this
    record does not claim is a mode `begin_authorization` will refuse.

    Nothing is written until the whole document has been accepted. A document
    that names somebody else, or that points the exchange at plain http, leaves
    no half-configured provider behind for the next attempt to trip over.
    """
    _require_admin()
    allow_loopback = bool(cint(allow_insecure_loopback))
    document_url = _discovery_document_url(discovery_url)
    check_endpoint(document_url, _("Discovery URL"), allow_insecure_loopback=allow_loopback)

    status, document = call_provider("GET", document_url)
    if status != 200 or not document:
        refuse(
            "provider_unreachable",
            _("{0} did not answer with a discovery document.").format(document_url),
        )

    # RFC 8414 section 3.3. Without this one provider's document configures a
    # client that talks to another, which is the whole of the IdP mix-up attack.
    issuer = _issuer_of(document_url)
    claimed = (document.get("issuer") or "").strip()
    if not _same_issuer(claimed, issuer):
        refuse(
            "issuer_mismatch",
            _("The discovery document at {0} claims to be issued by somebody else.").format(
                document_url
            ),
        )

    # An endpoint the document no longer advertises is cleared rather than left
    # standing, so the record says what the provider offers today.
    endpoints = {
        fieldname: (document.get(fieldname) or "").strip() for fieldname in DISCOVERED_ENDPOINTS
    }
    # The transport rule again, because the document is where a downgrade would
    # arrive: fetched over TLS, and then naming an http token endpoint.
    meta = frappe.get_meta("HD AI OAuth Provider")
    for fieldname, url in endpoints.items():
        check_endpoint(url, meta.get_label(fieldname), allow_insecure_loopback=allow_loopback)

    grants = document.get("grant_types_supported") or []
    challenges = document.get("code_challenge_methods_supported") or []
    return upsert_provider(
        provider_name=provider_name,
        preset=DEFAULT_PRESET,
        client_id=client_id,
        client_secret=client_secret,
        issuer=claimed,
        discovery_url=document_url,
        scope=scope,
        redirect_uri=redirect_uri,
        token_body_encoding=token_body_encoding,
        allow_insecure_loopback=allow_loopback,
        supports_pkce=int("S256" in challenges),
        supports_redirect=int(
            "authorization_code" in grants and bool(endpoints["authorization_endpoint"])
        ),
        supports_device_code=int(
            DEVICE_GRANT in grants and bool(endpoints["device_authorization_endpoint"])
        ),
        enabled=enabled,
        **endpoints,
    )


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
