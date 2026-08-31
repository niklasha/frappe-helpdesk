"""Helpdesk's half of the raphain contract.

raphain is a Rust library crate: it has no service a Python process can call.
What it publishes is a configuration contract, so this module owns the part
Helpdesk can own honestly — describing the engines, and emitting the documents
a raphain-embedding runner consumes. No inference happens here.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import cint
from frappe.utils.password import get_decrypted_password

from helpdesk.api.governance import log_configuration_change
from helpdesk.utils import agent_only, is_admin

ENGINE_FIELDS = ["engine_name", "kind", "model", "base_url", "is_default"]

REGISTRY_FIELDS = [
    "engine_name",
    "kind",
    "model",
    "base_url",
    "auth_type",
    "auth_env",
    "auth_header",
    "auth_oauth_provider",
    "auth_access_token_env",
    "auth_refresh_token_env",
    "auth_expires_at_unix",
    "auth_refresh",
    "auth_connection_status",
    "auth_account_id",
    "parameters",
    "headers",
    "options",
    "pricing",
]

DOCUMENT_FIELDS = ("parameters", "headers", "options", "pricing")

# What a bound OAuth provider states about the backend it fronts. raphain's
# ProviderConfig can carry every one of these as ordinary configuration, which
# is why the Codex quirks need no builder the registry document cannot reach.
BACKEND_FIELDS = [
    "engine_base_url",
    "engine_headers",
    "engine_options",
    "engine_parameters",
]

# raphain reads the ChatGPT account out of the token's own claims rather than
# off the provider record, so this header is per engine and exists only once a
# connection has actually produced an account to name.
ACCOUNT_HEADER = "chatgpt-account-id"


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
    auth_oauth_provider: str | None = None,
    auth_access_token_env: str | None = None,
    auth_access_token: str | None = None,
    auth_refresh_token_env: str | None = None,
    auth_expires_at_unix: int | None = None,
    auth_refresh: dict | list | str | None = None,
    parameters: dict | list | str | None = None,
    headers: dict | list | str | None = None,
    options: dict | list | str | None = None,
    pricing: dict | list | str | None = None,
    is_default: int | bool | None = None,
    enabled: int | bool | None = None,
) -> dict:
    """Create or update one AI engine, and return it as configured.

    Which model the helpdesk speaks to is exactly the kind of change an
    auditor asks about later, so every upsert is recorded.
    """
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
    if auth_oauth_provider is not None:
        doc.auth_oauth_provider = auth_oauth_provider
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
    if is_default is not None:
        doc.is_default = cint(is_default)
    if enabled is not None:
        doc.enabled = cint(enabled)
    doc.save(ignore_permissions=True)
    log_configuration_change(
        "HD AI Engine",
        doc.name,
        details={
            "kind": doc.kind,
            "model": doc.model,
            "base_url": doc.base_url,
            "auth_type": doc.auth_type,
            "is_default": cint(doc.is_default),
            "enabled": cint(doc.enabled),
        },
    )
    return doc.as_dict()


@frappe.whitelist()
@agent_only
def default_engine() -> str | None:
    """Return the engine raphain should fall back to, or None when there is none."""
    return frappe.db.get_value(
        "HD AI Engine", {"is_default": 1, "enabled": 1}, "engine_name"
    )


def _stored_document(value):
    """Return a stored JSON docfield as data, or None when it holds nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _stored_secret(engine, fieldname):
    """Read one encrypted docfield back, or None when the engine has none."""
    return get_decrypted_password(
        "HD AI Engine", engine.engine_name, fieldname, raise_exception=False
    )


def _token_is_live(engine) -> bool:
    """Whether the stored access token is still one a runner could use.

    A token an administrator typed in is exported as it was configured: Helpdesk
    was told nothing about its life and has no standing to withhold it. One
    Helpdesk obtained through a provider it knows about is different — it knows
    exactly when that token dies, and there is no refresh block behind it, so a
    dead one exported as live is a runner sending a credential that stopped
    working hours ago on every request.
    """
    if not engine.get("auth_oauth_provider"):
        return True
    return (
        engine.get("auth_connection_status") == "connected"
        and cint(engine.get("auth_expires_at_unix")) > int(time.time())
    )


def _oauth_block(engine, include_secrets=False) -> dict:
    """Describe an OAuth engine in raphain's oauth variant, which has no env/value.

    A connection Helpdesk obtained itself carries no refresh block and no refresh
    token: OAuthRefreshConfig posts a form and carries no headers, so a block
    emitted for a JSON token endpoint would fail the first time the runner ran
    it. Helpdesk refreshes those connections instead, and exports nothing
    downstream could not honour.
    """
    helpdesk_refreshes = bool(engine.get("auth_oauth_provider"))
    auth = {"type": "oauth"}
    if engine.auth_access_token_env:
        auth["access_token_env"] = engine.auth_access_token_env
    if engine.auth_refresh_token_env and not helpdesk_refreshes:
        auth["refresh_token_env"] = engine.auth_refresh_token_env
    if engine.auth_expires_at_unix:
        auth["expires_at_unix"] = cint(engine.auth_expires_at_unix)
    refresh = _stored_document(engine.get("auth_refresh"))
    if refresh and not helpdesk_refreshes:
        auth["refresh"] = refresh
    if include_secrets and _token_is_live(engine):
        access_token = _stored_secret(engine, "auth_access_token")
        if access_token:
            auth["access_token"] = access_token
    return auth


def _auth_block(engine, include_secrets=False):
    """Describe how the runner authenticates, as a reference rather than a secret.

    raphain's AuthConfig is a tagged union: each auth type has its own set of
    keys, and a key belonging to another variant makes the document invalid.
    """
    auth_type = engine.auth_type or "none"
    if auth_type == "none":
        return None
    if auth_type == "oauth":
        return _oauth_block(engine, include_secrets=include_secrets)
    auth = {"type": auth_type}
    if engine.auth_env:
        auth["env"] = engine.auth_env
    if auth_type == "api_key" and engine.auth_header:
        auth["header"] = engine.auth_header
    if include_secrets:
        secret = _stored_secret(engine, "auth_secret")
        if secret:
            auth["value"] = secret
    return auth


def _backend_shape(engine) -> dict:
    """What the engine's OAuth provider requires of the backend it authenticates to.

    An engine with no provider has no such requirements and is exported exactly
    as it was configured: the Codex wire quirks are facts about one backend, not
    about every engine Helpdesk describes.
    """
    if not engine.get("auth_oauth_provider"):
        return {}
    backend = frappe.db.get_value(
        "HD AI OAuth Provider", engine.auth_oauth_provider, BACKEND_FIELDS, as_dict=True
    )
    return backend or {}


def _layered(configured, required):
    """Lay an engine's own block over the one its backend requires.

    The backend's block is a default rather than an override. An administrator
    who typed a value meant it, and the two contradictions that would actually
    break the Codex backend are refused when the engine is saved — where somebody
    is watching — instead of being rewritten here where nobody would see it.
    `extra` is merged a level deeper, because it is a bag of unrelated keys and
    replacing the whole bag would drop settings neither side is arguing about.
    """
    block = dict(required or {})
    for key, value in (configured or {}).items():
        if key == "extra" and isinstance(value, dict) and isinstance(block.get("extra"), dict):
            block["extra"] = {**block["extra"], **value}
        else:
            block[key] = value
    return block


def _backend_headers(configured, required, account_id):
    """Every header the backend needs, with the engine's own copy of one winning.

    A connection that produced no account id exports no account header: the
    Codex backend refuses a request without one, and a header naming nothing at
    all fails the same way while looking configured.
    """
    headers = list(configured or [])
    named = {row.get("name") for row in headers if isinstance(row, dict)}
    for row in required or []:
        if isinstance(row, dict) and row.get("name") not in named:
            headers.append(row)
            named.add(row.get("name"))
    if account_id and ACCOUNT_HEADER not in named:
        headers.append({"name": ACCOUNT_HEADER, "value": account_id})
    return headers


def _provider(engine, include_secrets=False):
    """Describe one engine as a raphain ProviderConfig, without null keys."""
    backend = _backend_shape(engine)
    provider = {"name": engine.engine_name, "kind": engine.kind, "model": engine.model}
    base_url = engine.base_url or backend.get("engine_base_url")
    if base_url:
        provider["base_url"] = base_url
    for fieldname in DOCUMENT_FIELDS:
        document = _stored_document(engine.get(fieldname))
        if document:
            provider[fieldname] = document
    if backend:
        headers = _backend_headers(
            provider.get("headers"),
            _stored_document(backend.get("engine_headers")),
            engine.get("auth_account_id"),
        )
        if headers:
            provider["headers"] = headers
        for key, fieldname in (("options", "engine_options"), ("parameters", "engine_parameters")):
            block = _layered(provider.get(key), _stored_document(backend.get(fieldname)))
            if block:
                provider[key] = block
    auth = _auth_block(engine, include_secrets=include_secrets)
    if auth:
        provider["auth"] = auth
    return provider


@frappe.whitelist()
@agent_only
def registry_document(include_secrets: int | bool = 0) -> dict:
    """Return the raphain RegistryConfig for the engines that are enabled.

    raphain reads this document straight off disk and rejects nulls, so a key
    whose value is unset is left out rather than exported as None. Inline
    secrets stay behind unless an administrator asks for them, so the usual
    export is safe to write to disk and hand to a runner.
    """
    include_secrets = cint(include_secrets)
    if include_secrets and not is_admin():
        frappe.throw(
            _("Only an administrator may export AI engine secrets."),
            frappe.PermissionError,
        )
    document = {}
    default = default_engine()
    if default:
        document["default"] = default
    engines = frappe.get_all(
        "HD AI Engine",
        filters={"enabled": 1},
        fields=REGISTRY_FIELDS,
        order_by="creation asc",
    )
    document["providers"] = [
        _provider(engine, include_secrets=include_secrets) for engine in engines
    ]
    return document
