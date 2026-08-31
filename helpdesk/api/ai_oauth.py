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

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, get_datetime, get_url, now_datetime
from frappe.utils.password import get_decrypted_password

from helpdesk.api.governance import log_configuration_change
from helpdesk.helpdesk.doctype.hd_ai_oauth_provider.hd_ai_oauth_provider import (
    check_endpoint,
    refuse,
)
from helpdesk.utils import is_admin, is_agent

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

# How far ahead of expiry the unattended sweep reaches. A token whose expiry is
# further out than this is nobody's business yet; one inside it is renewed while
# it still works, so the runner never holds a credential that died between ticks.
REFRESH_WINDOW_SECONDS = 300

# What a stored credential was obtained under. A token is only meaningful against
# the client it was issued to and the endpoints it was issued by, so a change to
# any of these is not an edit to a connection — it is a different provider under
# the same name, and the connections made under the old one end.
BINDING_FIELDS = (
    "client_id",
    "issuer",
    "authorization_endpoint",
    "token_endpoint",
    "device_authorization_endpoint",
    "revocation_endpoint",
    "redirect_uri",
)

# The endpoints a discovery document may name, and the flag each one feeds.
DISCOVERED_ENDPOINTS = (
    "authorization_endpoint",
    "token_endpoint",
    "device_authorization_endpoint",
    "revocation_endpoint",
)

# Where the provider's browser redirect lands when the mode is `redirect`. Guest
# reaches it, because that is who arrives on it: an unauthenticated browser.
CALLBACK_METHOD = "helpdesk.api.ai_oauth.oauth_redirect_callback"

# Where the callback sends the browser when it is done with it. A landing page
# the request could choose would be an open redirect signed by the helpdesk and
# reachable without logging in, so this one is a constant of this module and
# site-relative: the browser goes back into the app it started from.
CALLBACK_LANDING = "/helpdesk/tickets"

# The three ways a token can be acquired, and the flag that says a provider has
# each one. They are read in this order, so the mode named in a refusal reads the
# way the settings page lists them.
MODE_FLAGS = {
    "redirect": "supports_redirect",
    "loopback_paste": "supports_loopback_paste",
    "device_code": "supports_device_code",
}

# raphain reads the ChatGPT account out of the access token's own claims
# (src/auth.rs:317-344); the Codex backend refuses every request without it.
ACCOUNT_CLAIM = "https://api.openai.com/auth"
ACCOUNT_CLAIM_KEY = "chatgpt_account_id"

# The presets whose backend reads that claim. Only the ChatGPT surface does, so
# only there is a token without one useless the moment it is stored; every other
# provider mints tokens that carry no such claim and are perfectly good.
ACCOUNT_CLAIM_PRESETS = ("openai_codex",)

# What may be repeated into an HTTP header. RFC 7230's field-vchar and nothing
# else: a CR or an LF here is request splitting, and the claim is read out of an
# unverified JWT, so whoever controls the provider writes this value. The length
# is generous beside the account identifier OpenAI actually mints.
ACCOUNT_ID_PATTERN = re.compile(r"\A[\x21-\x7e]+\Z")
ACCOUNT_ID_MAX_LENGTH = 128

# RFC 7636 section 4.1 allows 43 to 128 characters; raphain's Challenge::new
# draws 72 bytes, which lands at 96 and leaves no room for a lucky guess.
VERIFIER_BYTES = 72

# The state is the only thing between an anonymous browser redirect and a stored
# credential, so it is drawn at the same strength as the verifier.
STATE_BYTES = 32

# RFC 8628 section 3.2's default, for a provider that names no interval.
DEVICE_INTERVAL_SECONDS = 5

# RFC 8628 section 3.5: the two errors a device poll keeps going through, and
# what slow_down costs. Everything else the token endpoint answers is the
# provider ending the grant, not asking for patience.
DEVICE_PENDING_ERRORS = ("authorization_pending", "slow_down")
DEVICE_SLOW_DOWN_SECONDS = 5

# The parameters of an authorization response that decide what happens next, and
# that a provider therefore sends exactly once. A second copy of any of them is
# somebody else's, put there to be the one a parser happens to pick.
LANDING_PARAMETERS = ("code", "state", "error")

# The fields a connection is read out of. The tokens are not among them: nothing
# that renders a connection has any use for one.
CONNECTION_FIELDS = [
    "engine_name",
    "auth_oauth_provider",
    "auth_oauth_mode",
    "auth_connection_status",
    "auth_granted_scope",
    "auth_account_id",
    "auth_expires_at_unix",
]

# What a disconnect offers the provider back, and the token_type_hint RFC 7009
# section 2.1 wants beside each one, in the order they are offered.
REVOCABLE_CREDENTIALS = (
    ("auth_refresh_token", "refresh_token"),
    ("auth_access_token", "access_token"),
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


def _require_agent() -> None:
    """The providers on offer and whether an engine is connected are page furniture.

    An agent may read both; anybody else may not, because the provider and the
    account behind it are facts about the organisation's own identity. The gate
    is spelled out here rather than taken from `agent_only` so that a refused
    read names the same rule, in the same marker, as a refused write.
    """
    if not is_agent():
        refuse(
            "not_permitted",
            _("You are not permitted to see this AI engine's connection."),
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


def _binding_of(provider) -> dict:
    """The configuration a token obtained from this provider would belong to."""
    return {fieldname: provider.get(fieldname) or "" for fieldname in BINDING_FIELDS}


def _end_connections(provider_name: str) -> None:
    """End every connection obtained under a provider that has just moved.

    `upsert_provider` is an upsert, so one field is all it takes to point the
    token endpoint at a host of the editor's choosing — and the next refresh
    would post a live refresh token to it. The access token goes now, because it
    was minted by somebody we no longer talk to; the refresh token stays where it
    is, unusable and unexported, because it is what tells the next refresh that
    this connection moved rather than that it was never made.
    """
    for engine_name in frappe.get_all(
        "HD AI Engine",
        filters={"auth_oauth_provider": provider_name, "auth_connection_status": "connected"},
        pluck="name",
    ):
        _end_connection(frappe.get_doc("HD AI Engine", engine_name))
        # Nobody pressed Koppla från on this engine, so the log is the only place
        # the administrator will find out that editing the provider took its
        # connection with it.
        _audit(engine_name, "oauth_disconnected", provider=provider_name, reason="provider_moved")


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
    existing = frappe.db.exists("HD AI OAuth Provider", provider_name)
    if existing:
        doc = frappe.get_doc("HD AI OAuth Provider", provider_name)
    else:
        doc = frappe.new_doc("HD AI OAuth Provider")
        doc.provider_name = provider_name
        doc.update(PRESETS.get(preset or DEFAULT_PRESET, {}))
    bound_to = _binding_of(doc)
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
    if existing and _binding_of(doc) != bound_to:
        _end_connections(doc.name)
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
def list_providers() -> list:
    """Return the providers an engine can be bound to, newest first.

    The client secret is not among the fields: nothing that reads this list has
    any use for it.
    """
    _require_agent()
    return frappe.get_all(
        "HD AI OAuth Provider",
        filters={"enabled": 1},
        fields=PROVIDER_FIELDS,
        order_by="creation desc",
    )


def _b64url(raw: bytes) -> str:
    """base64url without padding, which is what every OAuth document means by it."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pkce_challenge(verifier: str) -> str:
    """RFC 7636's S256 transformation, which the provider recomputes for itself."""
    return _b64url(hashlib.sha256(verifier.encode()).digest())


def _hashed(state: str) -> str:
    """What the grant row remembers about the state it issued.

    A state stored as itself is a credential sitting in a table that is read for
    every other reason, and the row outlives the ten minutes the grant is worth
    anything. The digest answers the only question ever asked of it.
    """
    return hashlib.sha256(state.encode()).hexdigest()


def _oauth_engine(engine_name: str) -> dict:
    """Return the engine a grant is being obtained for, or say why there is none."""
    engine = frappe.db.get_value(
        "HD AI Engine", engine_name, ["name", "auth_oauth_provider"], as_dict=True
    )
    if not engine:
        frappe.throw(
            _("{0} is not an AI engine.").format(engine_name), frappe.DoesNotExistError
        )
    if not engine.auth_oauth_provider:
        frappe.throw(
            _("{0} is not bound to an OAuth provider, so there is nothing to authorize.").format(
                engine_name
            )
        )
    return engine


def _choose_mode(provider, mode: str | None) -> str:
    """Settle how the token will be acquired, out of what the provider actually has.

    The mode is a property of the provider and not of the caller: OpenAI has no
    device endpoint, and its public client redirects to a port on the
    administrator's own machine that no server-side callback can receive. One
    offered mode needs no naming. More than one does, because guessing would send
    an administrator down a flow their provider does not support. A mode that is
    not offered is refused now rather than three calls later, when the endpoint it
    needs turns out to be empty.
    """
    offered = [name for name, flag in MODE_FLAGS.items() if cint(provider.get(flag))]
    if mode:
        if mode not in offered:
            refuse(
                "mode_unsupported",
                _("{0} does not offer the {1} authorization mode.").format(provider.name, mode),
            )
        return mode
    if not offered:
        refuse(
            "mode_unsupported",
            _("{0} offers no way to obtain a token.").format(provider.name),
        )
    if len(offered) > 1:
        refuse(
            "mode_required",
            _("{0} offers more than one way to authorize; ask for one of: {1}.").format(
                provider.name, ", ".join(offered)
            ),
        )
    return offered[0]


def _redirect_uri_for(provider, mode: str) -> str | None:
    """Where the provider sends the browser once the administrator has approved.

    Redirect mode lands back on this site. Paste-back mode lands wherever the
    client's registration says, which for the public Codex client is a loopback
    port on the administrator's own machine.
    """
    if mode == "redirect":
        return get_url(f"/api/method/{CALLBACK_METHOD}")
    return provider.redirect_uri


def _provider_secret(provider) -> str | None:
    """Read the client secret back, accepting that a public client has none."""
    return get_decrypted_password(
        "HD AI OAuth Provider", provider.name, "client_secret", raise_exception=False
    )


def _engine_secret(engine, fieldname: str) -> str | None:
    """Read one of the credentials a connection left on the engine."""
    return get_decrypted_password(
        "HD AI Engine", engine.name, fieldname, raise_exception=False
    )


def _grant_secret(grant, fieldname: str) -> str | None:
    """Read one of the halves the grant keeps server-side."""
    return get_decrypted_password(
        "HD AI OAuth Grant", grant.name, fieldname, raise_exception=False
    )


def _post_grant(provider, url: str, body: dict) -> tuple[int, dict]:
    """Post one grant request in the body encoding the provider actually reads.

    RFC 6749 says form, and raphain's two vendor presets both say JSON — the
    Codex token endpoint answers an error to a form body. Accept goes on every
    one of them, because a provider that answers a token request in HTML has told
    the caller nothing it can act on.
    """
    headers = {"Accept": "application/json"}
    headers.update(provider.parsed_document("extra_token_headers") or {})
    options: dict = {"headers": headers}
    if (provider.token_body_encoding or "form") == "json":
        options["json"] = body
    else:
        options["data"] = body
    return call_provider("POST", url, **options)


def _authorize_url(provider, params: dict) -> str:
    """Put the request's parameters on the provider's own authorization endpoint.

    The endpoint may carry a query of its own — a policy, a tenant, an audience —
    so the parameters are merged into it rather than appended after a guessed
    separator.
    """
    parts = urlsplit(provider.authorization_endpoint)
    query = parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query), fragment=""))


def _audit(
    engine_name: str, action: str, performed_by: str | None = None, **facts: object
) -> None:
    """Record one connection change against the engine it happened to.

    Binding an engine to somebody's external account is a change to the rules the
    helpdesk runs on, so it belongs in the same log as every other one — and the
    person is the point of the row: it says whose identity the inference is now
    billed to and answers for. Nothing a credential could be reconstructed from
    goes in `facts`; a connection is described by the provider it was made with
    and the account it was made for, never by the token that carries it.

    The session is not always the person. A redirect grant is finished by an
    anonymous browser following a 302, and a row saying Guest connected the
    engine names nobody, so callers hand over the administrator who began the
    grant and the row is written as them.
    """
    session_user = frappe.session.user
    performed_by = performed_by or session_user
    if performed_by != session_user:
        frappe.set_user(performed_by)
    try:
        log_configuration_change(
            "HD AI Engine", engine_name, details={"action": action, **facts}
        )
    finally:
        if performed_by != session_user:
            frappe.set_user(session_user)


def _audit_refusal(grant, slug: str) -> None:
    """Record a grant that was refused, durably.

    A forged state and a proof the provider would not take are the events that
    say somebody is trying, and they are exactly the ones a log of successes
    loses. Frappe rolls the request back when the refusal is thrown, so the row
    is committed here or it is never written at all.

    The reason is the refusal's own marker rather than its prose: it is the token
    that names the rule, and it is not somebody's translated error message.
    """
    _audit(
        grant.engine,
        "oauth_connect_refused",
        performed_by=grant.owner,
        provider=grant.provider,
        mode=grant.mode,
        reason=slug,
    )
    frappe.db.commit()


def _finish_grant(grant, status: str) -> None:
    """End a grant and drop the halves only a live one needs.

    The verifier has proved what it was for, the device code has been redeemed or
    abandoned. A finished row that still holds either is a second copy of a
    credential, in a table nobody thinks of as one.
    """
    grant.status = status
    grant.code_verifier = None
    grant.device_code = None
    grant.next_poll_at = None
    grant.save(ignore_permissions=True)


def _fail_grant(grant, slug: str, message: str, status: str = "failed") -> None:
    """End the grant, durably, and then refuse.

    Frappe rolls the request back when the refusal is thrown, so a grant marked
    failed inside the same transaction would be pending again the next time
    somebody presented the same code. The commit `_audit_refusal` ends on is what
    makes both the ending and the record of it survive the exception that caused
    them.
    """
    _finish_grant(grant, status)
    _audit_refusal(grant, slug)
    refuse(slug, message)


def _retire_pending_grants(engine_name: str) -> None:
    """One click, one live proof.

    An administrator who presses Anslut twice must not leave two states and two
    verifiers behind, each of them redeemable for a connection nobody is watching.
    """
    for stale in frappe.get_all(
        "HD AI OAuth Grant", filters={"engine": engine_name, "status": "pending"}, pluck="name"
    ):
        _finish_grant(frappe.get_doc("HD AI OAuth Grant", stale), "failed")


def _redeemable_grant(name: str):
    """Return the grant this response may be spent against, or refuse to.

    A grant is redeemable once and only while it is young. Both rules exist for
    the same reason: a code and a state sit in a browser history, a proxy log or
    a chat message long after the administrator has stopped watching.
    """
    grant = frappe.get_doc("HD AI OAuth Grant", name)
    if grant.status != "pending":
        refuse("grant_spent", _("This authorization has already been finished."))
    if get_datetime(grant.expires_at) < now_datetime():
        _fail_grant(
            grant,
            "grant_expired",
            _("This authorization took too long and is no longer valid."),
            status="expired",
        )
    return grant


def _check_state(grant, state: str | None) -> None:
    """The state is the only thing tying this response to the request we made.

    It is compared as a digest, in constant time, and before anything is sent
    anywhere: a forged state means the response belongs to somebody else's
    authorization, and spending the code to discover that would hand them the
    exchange they were after.
    """
    if not state or not hmac.compare_digest(_hashed(state), grant.state_hash or ""):
        # The grant stays pending: the response was somebody else's, so it is no
        # reason to take the administrator's own authorization away from them.
        # The attempt is still recorded, and the state it presented is not — that
        # is a credential wherever it is written down.
        _audit_refusal(grant, "state_mismatch")
        refuse(
            "state_mismatch",
            _("This authorization response does not belong to this request."),
        )


def _is_seconds(value: object) -> bool:
    """Whether a token response's expires_in is a number of seconds at all."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        return value.strip().isdigit() and int(value) > 0
    return False


def _unverified_claims(access_token: str) -> dict:
    """Read a JWT payload without verifying it, the way raphain does.

    The signature cannot be checked here — the ChatGPT bearer is signed with a key
    this deployment has no reason to hold — so nothing read out of it is trusted
    further than the provider that minted it a moment ago. A token that is not a
    JWT at all is not an error: most providers' access tokens are opaque.
    """
    segments = (access_token or "").split(".")
    if len(segments) < 2:
        return {}
    payload = segments[1] + "=" * (-len(segments[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except (ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _account_claim(access_token: str) -> object:
    """The chatgpt_account_id claim exactly as it arrived, whatever shape that is.

    Nothing is coerced here. A claim that is not a string has to be told apart
    from one that is not there at all: the first is a provider saying something
    this code will not repeat, the second is a token that cannot address the
    Codex backend.
    """
    auth = _unverified_claims(access_token).get(ACCOUNT_CLAIM)
    if not isinstance(auth, dict):
        return None
    return auth.get(ACCOUNT_CLAIM_KEY)


def _account_id(provider, access_token: str) -> str:
    """The ChatGPT account the Codex backend wants named on every request.

    The claim becomes an HTTP header value and is read out of a JWT nobody
    verified, so it is checked as one before it is stored: a newline in it is a
    second request smuggled into the first. The value itself is never repeated
    into the refusal — the whole point is that it does not belong in a string
    somebody else's parser will read.
    """
    claim = _account_claim(access_token)
    if claim is None or claim == "":
        if (provider.get("preset") or "") in ACCOUNT_CLAIM_PRESETS:
            refuse(
                "no_account_id",
                _("{0} minted a token that names no ChatGPT account.").format(provider.name),
            )
        return ""
    if (
        not isinstance(claim, str)
        or len(claim) > ACCOUNT_ID_MAX_LENGTH
        or not ACCOUNT_ID_PATTERN.match(claim)
    ):
        refuse(
            "account_id_invalid",
            _("{0} named an account this token cannot carry in a header.").format(provider.name),
        )
    return claim


def _scope_was_granted(requested: str | None, granted: str | None) -> bool:
    """Whether the provider gave every scope that was asked for.

    RFC 6749 section 5.1: a response naming no scope granted the one requested,
    so an absent scope is not a downgrade. A narrower one is, and it fails at the
    first inference call hours later for reasons nothing here can see.
    """
    asked = set((requested or "").split())
    if not asked or not (granted or "").strip():
        return True
    return asked <= set(granted.split())


def _token_from(provider, answer: dict, requested_scope: str | None = None) -> dict:
    """Read a token response, refusing one that is not a connection.

    A token response is a document somebody else wrote, and every rule here is a
    failure that would otherwise land on a user instead of on the administrator
    who pressed Connect. raphain treats an empty access token as a hard error; an
    absent or non-numeric expires_in leaves a choice between "already expired"
    and "never expires", neither of which is a fact about this token; a granted
    scope narrower than the one asked for is a connection that 403s at the first
    inference call; and a Codex token naming no account cannot address the
    backend at all. Ask the provider again rather than store a guess.
    """
    access_token = answer.get("access_token")
    access_token = access_token.strip() if isinstance(access_token, str) else ""
    if not access_token or not _is_seconds(answer.get("expires_in")):
        refuse(
            "token_response_invalid",
            _("{0} did not answer with a usable token.").format(provider.name),
        )
    scope = answer.get("scope")
    scope = scope if isinstance(scope, str) else ""
    if requested_scope is None:
        requested_scope = provider.scope
    if not _scope_was_granted(requested_scope, scope):
        refuse(
            "scope_not_granted",
            _("{0} granted less than this connection asked for.").format(provider.name),
        )
    refresh_token = answer.get("refresh_token")
    return {
        "access_token": access_token,
        "refresh_token": refresh_token if isinstance(refresh_token, str) else "",
        "scope": scope,
        "expires_at_unix": int(time.time()) + int(float(answer["expires_in"])),
        "account_id": _account_id(provider, access_token),
    }


def _read_token(grant, provider, answer: dict) -> dict:
    """Read the token response this grant paid for, and end the grant if it is not one.

    The code was spent at the provider the moment the exchange was posted, so a
    response that fails any of the rules above leaves a grant with nothing left
    to redeem rather than one that sits pending and looks retryable. The commit
    is what makes that survive the rollback the refusal causes, exactly as it is
    for an exchange the provider itself rejected.
    """
    try:
        return _token_from(provider, answer, requested_scope=grant.requested_scope)
    except frappe.ValidationError:
        _finish_grant(grant, "failed")
        frappe.db.commit()
        raise


def _write_token(engine, token: dict) -> None:
    """Put a minted token on the engine it was obtained for.

    The tokens go through the document API rather than straight at the columns,
    because that is what encrypts them; a set_value here would leave both
    credentials readable to anybody who can read the table.
    """
    engine.auth_access_token = token["access_token"]
    if token["refresh_token"]:
        # Absent means the provider is keeping the one it already gave us, not
        # that it has taken it away.
        engine.auth_refresh_token = token["refresh_token"]
    engine.auth_expires_at_unix = token["expires_at_unix"]
    engine.auth_granted_scope = token["scope"]
    engine.auth_account_id = token["account_id"]
    engine.auth_connection_status = "connected"


def _end_connection(engine, forget_refresh_token: bool = False) -> None:
    """Stop this engine being connected, and take the access token away with it.

    Whatever ended the connection, the token exported from here is the only
    credential the runner has and nothing renews it downstream — so it goes at
    the same moment the connection does, rather than staying live-looking in a
    registry document until somebody notices the 401s.
    """
    engine.auth_access_token = None
    engine.auth_expires_at_unix = 0
    engine.auth_connection_status = "disconnected"
    if forget_refresh_token:
        engine.auth_refresh_token = None
    engine.save(ignore_permissions=True)


def _distrust_token(engine) -> None:
    """Keep the connection, and stop vouching for the token it is holding.

    A provider that did not answer said nothing about the credential, so the
    connection stands and the sweep will try again. What cannot stand is the
    access token: we tried to renew it because it is about to die, could not, and
    exporting it as live in the meantime hands a runner a credential that fails
    silently for as long as nobody looks.
    """
    engine.auth_access_token = None
    engine.auth_expires_at_unix = 0
    engine.save(ignore_permissions=True)


def _store_connection(grant, provider, answer: dict) -> dict:
    """Write a minted token onto the engine this grant was obtained for.

    The response is read in full before the engine is touched: a refusal halfway
    through would otherwise leave an engine holding a token the rest of the
    document says is unusable.
    """
    token = _read_token(grant, provider, answer)
    engine = frappe.get_doc("HD AI Engine", grant.engine)
    _write_token(engine, token)
    engine.auth_oauth_mode = grant.mode
    engine.save(ignore_permissions=True)
    _audit(
        engine.name,
        "oauth_connected",
        performed_by=grant.owner,
        provider=grant.provider,
        mode=grant.mode,
        account_id=engine.auth_account_id,
        granted_scope=engine.auth_granted_scope,
    )
    return _connection(engine, grant=grant.name)


def _connection_state(engine) -> str:
    """A connection is only connected for as long as the token it holds is alive."""
    status = engine.get("auth_connection_status") or "disconnected"
    if status != "connected":
        return status
    return "connected" if cint(engine.get("auth_expires_at_unix")) > int(time.time()) else "expired"


def _connection(engine, grant: str | None = None) -> dict:
    """What may be said about a connection: facts about it, never a piece of it."""
    answer = {
        "engine": engine.get("engine_name"),
        "provider": engine.get("auth_oauth_provider"),
        "mode": engine.get("auth_oauth_mode"),
        "status": _connection_state(engine),
        "granted_scope": engine.get("auth_granted_scope"),
        "account_id": engine.get("auth_account_id"),
        "expires_at_unix": cint(engine.get("auth_expires_at_unix")),
    }
    if grant:
        answer["grant"] = grant
    return answer


def _begin_code_grant(engine, provider, mode: str) -> dict:
    """Build one authorization request, and keep the half the browser may not see."""
    state = secrets.token_urlsafe(STATE_BYTES)
    verifier = secrets.token_urlsafe(VERIFIER_BYTES) if cint(provider.supports_pkce) else None
    redirect_uri = _redirect_uri_for(provider, mode)
    grant = frappe.new_doc("HD AI OAuth Grant")
    grant.update(
        {
            "engine": engine.name,
            "provider": provider.name,
            "mode": mode,
            "status": "pending",
            "state_hash": _hashed(state),
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "requested_scope": provider.scope,
        }
    )
    grant.insert(ignore_permissions=True)
    params = {"response_type": "code", "client_id": provider.client_id or "", "state": state}
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    if provider.scope:
        params["scope"] = provider.scope
    if verifier:
        params["code_challenge"] = _pkce_challenge(verifier)
        params["code_challenge_method"] = "S256"
    # The Codex flow mints a token with no chatgpt_account_id claim unless these
    # are asked for, and every backend call then fails on a missing header.
    params.update(provider.parsed_document("extra_authorize_params") or {})
    return {
        "grant": grant.name,
        "engine": engine.name,
        "provider": provider.name,
        "mode": mode,
        "authorize_url": _authorize_url(provider, params),
    }


def _begin_device_grant(engine, provider) -> dict:
    """RFC 8628: ask for a code the administrator types in on another device.

    The user code is the half a person reads out loud. The device code is the
    secret that redeems the grant, so it stays here.
    """
    body = {"client_id": provider.client_id or ""}
    if provider.scope:
        body["scope"] = provider.scope
    status, answer = _post_grant(provider, provider.device_authorization_endpoint, body)
    device_code = answer.get("device_code")
    if status != 200 or not isinstance(device_code, str) or not device_code:
        refuse(
            "provider_unreachable",
            _("{0} did not issue a device code.").format(provider.name),
        )
    interval = cint(answer.get("interval")) or DEVICE_INTERVAL_SECONDS
    grant = frappe.new_doc("HD AI OAuth Grant")
    grant.update(
        {
            "engine": engine.name,
            "provider": provider.name,
            "mode": "device_code",
            "status": "pending",
            "device_code": device_code,
            "user_code": answer.get("user_code"),
            "verification_uri": answer.get("verification_uri"),
            "requested_scope": provider.scope,
            "interval": interval,
            # RFC 8628 section 3.5: the first poll waits out one interval, so a
            # client that polls the moment it has a code is still well behaved.
            "next_poll_at": add_to_date(now_datetime(), seconds=interval),
        }
    )
    if _is_seconds(answer.get("expires_in")):
        grant.expires_at = add_to_date(now_datetime(), seconds=int(float(answer["expires_in"])))
    grant.insert(ignore_permissions=True)
    return {
        "grant": grant.name,
        "engine": engine.name,
        "provider": provider.name,
        "mode": "device_code",
        "user_code": grant.user_code,
        "verification_uri": grant.verification_uri,
        "verification_uri_complete": answer.get("verification_uri_complete"),
        "interval": interval,
    }


@frappe.whitelist(methods=["POST"])
def begin_authorization(engine_name: str, mode: str | None = None) -> dict:
    """Start one authorization and hand back only the half a browser may carry.

    Every parameter of the request comes off the provider record. There is no
    argument here to point the authorize URL somewhere else, because a URL that
    went wherever the caller named — carrying the organisation's own client id —
    is a phishing primitive that borrows Helpdesk's credibility.
    """
    _require_admin()
    engine = _oauth_engine(engine_name)
    provider = frappe.get_doc("HD AI OAuth Provider", engine.auth_oauth_provider)
    mode = _choose_mode(provider, mode)
    _retire_pending_grants(engine.name)
    if mode == "device_code":
        return _begin_device_grant(engine, provider)
    return _begin_code_grant(engine, provider, mode)


def _exchange_code(pending, code: str, state: str | None) -> dict:
    """Spend one authorization code, however the administrator came back with it.

    The grant is finished the moment the exchange is attempted, whichever way it
    goes: a code that has been presented is spent whether or not the provider
    liked it, and a second attempt with the same pair finds nothing left to
    redeem. Nothing is written onto the engine until the whole token response has
    been read and accepted.
    """
    provider = frappe.get_doc("HD AI OAuth Provider", pending.provider)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": provider.client_id or "",
    }
    if pending.redirect_uri:
        # RFC 6749 section 4.1.3: both legs of the flow name the same one.
        body["redirect_uri"] = pending.redirect_uri
    verifier = _grant_secret(pending, "code_verifier")
    if verifier:
        body["code_verifier"] = verifier
    client_secret = _provider_secret(provider)
    if client_secret:
        body["client_secret"] = client_secret
    if cint(provider.state_in_token_body) and state:
        # An Anthropic extension: the state is echoed in the exchange body too.
        body["state"] = state
    status, answer = _post_grant(provider, provider.token_endpoint, body)
    if status != 200:
        # The code was minted by this provider moments ago and is being presented
        # for the first time, so the proof it could not verify is the PKCE one.
        _fail_grant(
            pending,
            "pkce_failed",
            _("{0} refused the authorization code.").format(provider.name),
        )
    connection = _store_connection(pending, provider, answer)
    _finish_grant(pending, "connected")
    return connection


@frappe.whitelist(methods=["POST"])
def complete_authorization(grant: str, code: str, state: str | None = None) -> dict:
    """Exchange an authorization code for a token, once."""
    _require_admin()
    pending = _redeemable_grant(grant)
    _check_state(pending, state)
    return _exchange_code(pending, code, state)


def _landed_where_it_was_sent(landed: str, expected: str) -> bool:
    """Whether the pasted string is the address the provider was told to land on.

    Scheme, host, port and path, all four of them. A phishing page hands the
    administrator a URL that looks like the one they were told to copy, and a
    check that reads only the query accepts every one of them — including, in an
    IdP-mixup, a code minted by an entirely different flow. The path is compared
    as it arrived rather than resolved: `/auth/callback/../..` is a claim about
    somewhere else however a filesystem would read it.
    """
    try:
        left, right = urlsplit(landed), urlsplit(expected)
        same_port = left.port == right.port
    except ValueError:
        # An authority no parser agrees on is not this grant's redirect URI, and
        # asking which one it is has no answer worth acting on.
        return False
    return (
        left.scheme.lower() == right.scheme.lower()
        and (left.hostname or "").lower() == (right.hostname or "").lower()
        and same_port
        and left.path == right.path
    )


def _landing_parameters(grant, redirect_url: str) -> dict:
    """Read the query of the URL the browser landed on, once it is the right URL.

    The administrator pastes this string under exactly the conditions social
    engineering is good at producing, so where it came from is settled before
    anything inside it is read, and nothing reaches the provider until it has
    passed. The query only: a fragment never leaves the browser, so a code found
    there was put there by whoever built the URL rather than by the provider.
    """
    if not grant.redirect_uri or not _landed_where_it_was_sent(
        redirect_url or "", grant.redirect_uri
    ):
        refuse(
            "redirect_uri_mismatch",
            _("That is not the address this authorization was told to return to."),
        )
    values: dict = {}
    for name, value in parse_qsl(urlsplit(redirect_url).query, keep_blank_values=True):
        if name in LANDING_PARAMETERS and name in values:
            # Which of the two a parser picks is a coin toss between libraries;
            # refusing is the only answer that is the same everywhere.
            refuse(
                "duplicate_parameter",
                _("The pasted address carries {0} more than once.").format(name),
            )
        values[name] = value
    if values.get("error"):
        # RFC 6749 section 4.1.2.1. The description beside it is the provider's
        # prose about somebody's own account, so only the code is repeated — and
        # escaped, because the string it was read out of was typed by hand.
        refuse(
            "authorization_denied",
            _("The provider did not grant this authorization: {0}.").format(
                escape_html(values["error"])
            ),
        )
    if not values.get("code"):
        refuse("no_code", _("The pasted address carries no authorization code."))
    return values


@frappe.whitelist(methods=["POST"])
def complete_from_redirect_url(grant: str, redirect_url: str) -> dict:
    """Finish a grant from the one URL the administrator's browser landed on.

    This is the mode the customer's own case needs. The public Codex client's
    registered redirect is a loopback port on the administrator's machine, which
    no server-side callback can ever receive, so the browser lands somewhere
    nothing is listening and the administrator hands the address back. They still
    type no token, no expiry and no client secret: everything but this one string
    stays server-side.
    """
    _require_admin()
    pending = _redeemable_grant(grant)
    landed = _landing_parameters(pending, redirect_url)
    _check_state(pending, landed.get("state"))
    return _exchange_code(pending, landed["code"], landed.get("state"))


def _grant_for_redirect(state: str | None):
    """Find the grant this redirect belongs to, knowing nothing but its state.

    Guest arrives here with a query string and no session, so the state is both
    the name of the grant and the proof of it: the row is looked up by the digest
    stored when the authorize URL was built, and a state matching no row names
    nothing at all. Only redirect grants are reachable this way — a paste-back or
    device state is redeemed through an endpoint that asks who is calling, and
    letting an anonymous request spend one here would give away that gate.

    The refusal is the same either way. Telling an unauthenticated caller which
    of "no such state" and "not your mode" they hit is telling them how to
    enumerate the grants in flight.
    """
    name = (
        frappe.db.get_value(
            "HD AI OAuth Grant", {"state_hash": _hashed(state), "mode": "redirect"}, "name"
        )
        if state
        else None
    )
    if not name:
        refuse(
            "state_mismatch",
            _("This authorization response does not belong to this request."),
        )
    return _redeemable_grant(name)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def oauth_redirect_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> dict:
    """Receive the provider's redirect, as the anonymous browser that carries it.

    This is the one door in the module Guest may knock on, because that is who
    arrives on it: a browser following a 302, with no session and no CSRF token.
    The state is the whole of the credential standing in it, so it is looked up
    and spent exactly once, and every ending settles the grant before anything
    else happens — a state nobody issued, a code already redeemed and a grant
    nobody finished in time are each refused without the code ever being sent.

    `error_description` is accepted because the provider puts it on the query
    string, and is then never read. It, and `error` beside it, are attacker-typed
    text arriving on this site's own origin; repeating either into a page, a
    message or a log turns this callback into a reflection point that needs no
    account to reach.
    """
    pending = _grant_for_redirect(state)
    if error:
        # RFC 6749 section 4.1.2.1: the administrator pressed Deny, or the
        # provider ended it for them. Either way this grant has nothing left.
        _fail_grant(
            pending,
            "authorization_denied",
            _("The provider did not grant this authorization."),
        )
    if not code:
        _fail_grant(
            pending,
            "no_code",
            _("The provider's redirect carried no authorization code."),
        )
    connection = _exchange_code(pending, code, state)
    connection["redirect_to"] = CALLBACK_LANDING
    return connection


def _device_grant(name: str):
    """Return the grant this poll is for, or say why it cannot be polled.

    The redeemability rules are the ones every other grant is held to: a grant
    the provider already ended is spent, and one nobody approved in time is
    expired. Both are settled here, before anything is sent, because neither is
    worth a request to the provider.
    """
    grant = _redeemable_grant(name)
    if grant.mode != "device_code":
        refuse(
            "mode_unsupported",
            _("This authorization is not a device grant, so there is nothing to poll."),
        )
    return grant


def _due_to_poll(grant) -> bool:
    """Whether the interval the provider asked for has passed.

    RFC 8628 section 3.5's interval is not advice: polling faster than it earns a
    slow_down and then a rate limit, and the client that caused it is the one
    holding the only proof of an authorization in flight.
    """
    return not grant.next_poll_at or get_datetime(grant.next_poll_at) <= now_datetime()


def _schedule_next_poll(grant, interval: int) -> None:
    """Record what the provider is willing to be asked, and when it may be asked."""
    grant.interval = interval
    grant.next_poll_at = add_to_date(now_datetime(), seconds=interval)
    grant.save(ignore_permissions=True)


def _still_pending(grant, polled: bool) -> dict:
    """What a poll that found no answer yet may say — the device code excepted.

    `polled` is the part the settings page needs: a call that went no further
    than the interval looks exactly like one the provider answered, and without
    it the page cannot tell a provider that is thinking from one that is silent.
    """
    return {
        "grant": grant.name,
        "engine": grant.engine,
        "provider": grant.provider,
        "mode": grant.mode,
        "status": "authorization_pending",
        "polled": int(polled),
        "interval": cint(grant.interval),
    }


@frappe.whitelist(methods=["POST"])
def poll_device_authorization(grant: str) -> dict:
    """Ask the provider once whether the administrator has approved the grant yet.

    One call, one poll. The loop lives in the browser, where an administrator can
    stop it, rather than in a worker holding a request open for the fifteen
    minutes a device code lives. Every ending is durable: a grant the provider
    denied or let expire becomes failed and refuses the next poll as spent, so a
    page left open overnight cannot turn a denial into an unbounded loop against
    the provider.
    """
    _require_admin()
    pending = _device_grant(grant)
    if not _due_to_poll(pending):
        return _still_pending(pending, polled=False)

    provider = frappe.get_doc("HD AI OAuth Provider", pending.provider)
    body = {
        "grant_type": DEVICE_GRANT,
        "device_code": _grant_secret(pending, "device_code") or "",
        "client_id": provider.client_id or "",
    }
    client_secret = _provider_secret(provider)
    if client_secret:
        body["client_secret"] = client_secret
    status, answer = _post_grant(provider, provider.token_endpoint, body)
    if status == 200:
        connection = _store_connection(pending, provider, answer)
        _finish_grant(pending, "connected")
        connection["polled"] = 1
        return connection

    error = answer.get("error")
    error = error if isinstance(error, str) else ""
    if error == "access_denied":
        _fail_grant(
            pending,
            "device_denied",
            _("{0} reports that this authorization was refused.").format(provider.name),
        )
    if error == "expired_token":
        _fail_grant(
            pending,
            "device_expired",
            _("This device authorization expired before anybody approved it."),
        )
    if error not in DEVICE_PENDING_ERRORS:
        # An error nobody kept polling through in RFC 8628 is one the provider
        # will keep answering. Ending the grant is what stops the loop.
        _fail_grant(
            pending,
            "device_denied",
            _("{0} would not grant this device authorization.").format(provider.name),
        )
    interval = cint(pending.interval) or DEVICE_INTERVAL_SECONDS
    if error == "slow_down":
        # Section 3.5 again: slow_down means add five seconds, not try harder.
        interval += DEVICE_SLOW_DOWN_SECONDS
    _schedule_next_poll(pending, interval)
    return _still_pending(pending, polled=True)


def _refresh_body(engine, provider, refresh_token: str) -> dict:
    """RFC 6749 section 6, with the client the token was issued to named again."""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": provider.client_id or "",
    }
    # Section 6 lets the request name a scope, and forbids a wider one than was
    # granted. Asking for exactly what this connection already holds keeps a
    # provider that reads the parameter from quietly narrowing the token.
    scope = engine.auth_granted_scope or provider.scope
    if scope:
        body["scope"] = scope
    client_secret = _provider_secret(provider)
    if client_secret:
        body["client_secret"] = client_secret
    return body


def _refresh_connection(engine_name: str) -> dict:
    """Renew one engine's access token from the refresh token it is holding.

    Helpdesk does this rather than the runner because raphain's declarative
    refresh block cannot: OAuthRefreshConfig posts a form and carries no headers,
    and the Codex token endpoint reads JSON. The body encoding comes off the
    provider record for the same reason every other request in this module does —
    it is a fact about the provider, not about the code that talks to it.

    Every ending is durable and closed. A provider that refuses the credential
    ends the connection and takes the dead refresh token with it; a provider that
    says nothing has told us nothing about the credential, so that connection
    stands and only the access token stops being exported.
    """
    engine = frappe.get_doc("HD AI Engine", _oauth_engine(engine_name).name)
    provider = frappe.get_doc("HD AI OAuth Provider", engine.auth_oauth_provider)
    refresh_token = _engine_secret(engine, "auth_refresh_token")
    if refresh_token and engine.auth_connection_status == "disconnected":
        # A credential kept by a connection that is no longer live is one whose
        # provider was reconfigured under it. Posting it now would send it to
        # whichever host the edit named.
        refuse(
            "provider_repointed",
            _("{0} has been reconfigured since {1} was connected to it.").format(
                provider.name, engine.name
            ),
        )
    if not refresh_token:
        refuse(
            "refresh_failed",
            _("{0} holds no refresh token, so there is nothing to renew.").format(engine.name),
        )

    body = _refresh_body(engine, provider, refresh_token)
    try:
        status, answer = _post_grant(provider, provider.token_endpoint, body)
    except frappe.ValidationError:
        _distrust_token(engine)
        frappe.db.commit()
        raise
    try:
        if status != 200:
            # Revoked at the provider, rotated out of band, or simply too old.
            # The error beside it is the provider's prose about somebody's own
            # account, and the body it was answering carried the credential.
            refuse(
                "refresh_failed",
                _("{0} would not renew this connection.").format(provider.name),
            )
        token = _token_from(provider, answer, requested_scope=body.get("scope"))
    except frappe.ValidationError:
        # Frappe rolls the request back when the refusal is thrown, so an engine
        # disconnected inside the same transaction would be connected again — with
        # a credential the provider has just refused — the moment it returned.
        _end_connection(engine, forget_refresh_token=True)
        _audit(
            engine.name,
            "oauth_disconnected",
            provider=provider.name,
            reason="refresh_refused",
        )
        frappe.db.commit()
        raise
    _write_token(engine, token)
    engine.save(ignore_permissions=True)
    _audit(
        engine.name,
        "oauth_refreshed",
        provider=provider.name,
        granted_scope=engine.auth_granted_scope,
        expires_at_unix=cint(engine.auth_expires_at_unix),
    )
    return _connection(engine)


@frappe.whitelist(methods=["POST"])
def refresh_engine_token(engine_name: str) -> dict:
    """Renew one engine's token now, and say what the connection looks like after."""
    _require_admin()
    return _refresh_connection(engine_name)


def refresh_expiring_tokens() -> dict:
    """Renew every connection whose token is about to die. Unattended.

    This is the path that keeps a connection working between the day an
    administrator made it and the day they think about it again, so it runs as
    Administrator on a timer — and is deliberately not whitelisted, because a
    whitelisted sweep is a way for anybody who can reach the site to make it talk
    to every configured provider on demand.

    One provider being down is not a reason to leave every other engine's token
    to expire, so each engine is refreshed on its own and what went wrong is left
    where the next call can see it: on the engine. Nothing is written to the error
    log, because a traceback from here quotes the exchange it failed in.
    """
    due = frappe.get_all(
        "HD AI Engine",
        filters={
            "enabled": 1,
            "auth_type": "oauth",
            "auth_oauth_provider": ["is", "set"],
            "auth_connection_status": "connected",
            "auth_expires_at_unix": ["<", int(time.time()) + REFRESH_WINDOW_SECONDS],
        },
        pluck="name",
        order_by="creation asc",
    )
    swept: dict = {"refreshed": [], "failed": []}
    for engine_name in due:
        try:
            _refresh_connection(engine_name)
        except Exception:
            frappe.db.rollback()
            swept["failed"].append(engine_name)
        else:
            frappe.db.commit()
            swept["refreshed"].append(engine_name)
    return swept


def _held_credentials(engine) -> list:
    """Every credential this connection is still holding, and what each one is.

    The refresh token comes first because that is the one RFC 7009 section 2.1
    asks a provider to take the whole grant down with. The access token is
    offered after it all the same: that section says SHOULD, and a provider that
    does not cascade would otherwise be left with a live bearer nobody here can
    reach any more.
    """
    held = []
    for fieldname, hint in REVOCABLE_CREDENTIALS:
        token = _engine_secret(engine, fieldname)
        if token:
            held.append((token, hint))
    return held


def _revoke_at_provider(provider, credentials: list) -> None:
    """Tell the provider the credentials it minted are finished with. RFC 7009.

    Only where it said it takes them. A revocation endpoint is not something that
    can be guessed from a token endpoint, and posting a live credential to an
    address nobody advertised is exactly how one ends up somewhere it should not
    be — so a provider that advertised none is left alone.

    Nothing here decides whether the disconnect happened; that is already
    settled and committed by the time this runs. A provider that will not answer
    is not a reason to keep an access token exportable from Helpdesk.
    """
    if not provider.revocation_endpoint:
        return
    client_secret = _provider_secret(provider)
    for token, hint in credentials:
        body = {"token": token, "token_type_hint": hint, "client_id": provider.client_id or ""}
        if client_secret:
            body["client_secret"] = client_secret
        try:
            _post_grant(provider, provider.revocation_endpoint, body)
        except frappe.ValidationError:
            # Section 2.2 has a provider answer 200 even to a token it has never
            # heard of, so there is no answer here worth reading and no failure
            # worth reporting. The refusal is dropped rather than carried out on
            # a call that succeeded.
            frappe.clear_last_message()


@frappe.whitelist(methods=["POST"])
def disconnect_engine(engine_name: str) -> dict:
    """End one engine's connection, here and at the provider that granted it.

    Here first, and durably. An administrator who disconnects has decided this
    credential is finished, so the connection ends before a word is said to
    anybody else — an unreachable provider, or one that revokes the first token
    and then stops answering, must not leave a connection standing on credentials
    that are already gone upstream.
    """
    _require_admin()
    engine = frappe.get_doc("HD AI Engine", _oauth_engine(engine_name).name)
    provider = frappe.get_doc("HD AI OAuth Provider", engine.auth_oauth_provider)
    credentials = _held_credentials(engine)
    _end_connection(engine, forget_refresh_token=True)
    _audit(engine.name, "oauth_disconnected", provider=provider.name, reason="requested")
    frappe.db.commit()
    _revoke_at_provider(provider, credentials)
    return _connection(engine)


@frappe.whitelist()
def connection_status(engine_name: str) -> dict:
    """Say whether an engine holds a live token, without handing any of it over."""
    _require_agent()
    engine = frappe.db.get_value("HD AI Engine", engine_name, CONNECTION_FIELDS, as_dict=True)
    if not engine:
        frappe.throw(
            _("{0} is not an AI engine.").format(engine_name), frappe.DoesNotExistError
        )
    return _connection(engine)
