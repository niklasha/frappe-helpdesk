import ipaddress
import json
from urllib.parse import urlsplit

import frappe
from frappe import _
from frappe.model.document import Document

# Every refusal this feature makes carries `hd-oauth:<slug>` in its message,
# outside the translated prose. Frappe runs messages through `_()` and this ships
# to a Swedish-speaking site, so the rule that fired has to be named in something
# no translation touches.
REFUSAL_MARKER = "hd-oauth"

TOKEN_BODY_ENCODINGS = ("form", "json")

# The URLs Helpdesk itself fetches, or sends the administrator's browser to. All
# of them carry or produce a credential, so all of them are held to the same
# transport rule.
ENDPOINT_FIELDS = (
    "discovery_url",
    "authorization_endpoint",
    "token_endpoint",
    "device_authorization_endpoint",
    "revocation_endpoint",
)

OBJECT_FIELDS = (
    "extra_authorize_params",
    "extra_token_headers",
    "engine_options",
    "engine_parameters",
)


def refuse(slug: str, message: str, exception: type[Exception] | None = None) -> None:
    """Throw a refusal that names which rule refused, in a token nothing translates."""
    frappe.throw(f"{message} [{REFUSAL_MARKER}:{slug}]", exception or frappe.ValidationError)


def is_loopback(hostname: str | None) -> bool:
    """Decide RFC 8252's loopback question on the authority, never on a substring.

    "127.0.0.1.evil.example" and "http://evil.example/token#127.0.0.1" both
    contain a loopback address and neither one is loopback.
    """
    if not hostname:
        return False
    host = hostname.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_endpoint(
    url: str | None,
    label: str,
    allow_insecure_loopback: bool = False,
    loopback_exempt: bool = False,
) -> None:
    """Refuse a URL the grant cannot safely travel over.

    TLS everywhere, with RFC 8252's exception for the loopback interface. That
    exception is opt-in per provider for anything Helpdesk fetches, because
    otherwise every service bound to the app server's own interface becomes a
    legal token endpoint and gets posted the stored refresh token. A loopback
    *redirect* is the exception's original case — the browser goes there, not the
    server — so it needs no opt-in.
    """
    if not url:
        return
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        # An authority no parser agrees on — a bracketed address in the userinfo,
        # say — cannot be shown to be anywhere in particular, so it is refused
        # rather than guessed at.
        refuse("insecure_endpoint", _("{0} is not a URL Helpdesk can read.").format(label))
    scheme = (parts.scheme or "").lower()
    if scheme != "https":
        if scheme == "http" and is_loopback(hostname):
            if not (loopback_exempt or allow_insecure_loopback):
                refuse(
                    "loopback_not_allowed",
                    _("{0} is on the loopback interface, which this provider does not allow.").format(
                        label
                    ),
                )
        else:
            refuse("insecure_endpoint", _("{0} must be an https URL.").format(label))
    if parts.username or parts.password:
        refuse("endpoint_userinfo", _("{0} may not carry credentials in its authority.").format(label))


class HDAIOAuthProvider(Document):
    """One identity provider an AI engine's OAuth grant can be obtained from.

    The capability flags are the point of the record: OpenAI publishes no device
    authorization endpoint and its public Codex client redirects to the
    administrator's own machine, so a mode is offered only where it exists.
    """

    def validate(self):
        """A provider Helpdesk could not safely talk to is not a usable provider."""
        self.validate_encoding()
        self.validate_endpoints()
        self.validate_documents()

    def validate_encoding(self):
        """RFC 6749 says form; only the two vendor presets disagree."""
        encoding = self.token_body_encoding or "form"
        if encoding not in TOKEN_BODY_ENCODINGS:
            frappe.throw(
                _("{0} is not a supported token body encoding. Use one of: {1}.").format(
                    encoding, ", ".join(TOKEN_BODY_ENCODINGS)
                )
            )

    def validate_endpoints(self):
        """Every URL on the record is checked, not only the one just edited."""
        allow_insecure_loopback = bool(self.allow_insecure_loopback)
        for fieldname in ENDPOINT_FIELDS:
            check_endpoint(
                self.get(fieldname),
                self.meta.get_label(fieldname),
                allow_insecure_loopback=allow_insecure_loopback,
            )
        check_endpoint(
            self.redirect_uri,
            self.meta.get_label("redirect_uri"),
            allow_insecure_loopback=allow_insecure_loopback,
            loopback_exempt=True,
        )

    def parsed_document(self, fieldname):
        """Return one JSON docfield as data, refusing text that is not JSON."""
        value = self.get(fieldname)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except ValueError:
            frappe.throw(_("{0} is not valid JSON.").format(fieldname))

    def validate_documents(self):
        """The JSON blocks hold the shape the authorize URL and the export read."""
        for fieldname in OBJECT_FIELDS:
            document = self.parsed_document(fieldname)
            if document is None:
                continue
            if not isinstance(document, dict):
                frappe.throw(_("{0} must be a JSON object.").format(fieldname))
            self.set(fieldname, json.dumps(document))
        headers = self.parsed_document("engine_headers")
        if headers is None:
            return
        if not isinstance(headers, list) or not all(
            isinstance(entry, dict) and entry.get("name") and entry.get("value")
            for entry in headers
        ):
            frappe.throw(_("Engine headers must be a list of objects with a name and a value."))
        self.set("engine_headers", json.dumps(headers))
