import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

SUPPORTED_KINDS = (
    "openai",
    "openai_compatible",
    "openai_responses",
    "responses",
    "anthropic",
)

AUTH_TYPES = ("none", "api_key", "bearer", "oauth")
SECRET_AUTH_TYPES = ("api_key", "bearer")

# raphain's AuthConfig gives oauth its own variant: it carries tokens rather
# than the env/value pair api_key and bearer share, so the two sets of fields
# never appear on the same engine.
OAUTH_FIELDS = (
    "auth_access_token_env",
    "auth_access_token",
    "auth_refresh_token_env",
    "auth_expires_at_unix",
    "auth_refresh",
)

OBJECT_FIELDS = ("parameters", "options", "pricing")


class HDAIEngine(Document):
    """One raphain provider Helpdesk knows how to describe.

    Helpdesk is the configuration authority for the engines a
    raphain-embedding runner talks to; it never performs inference itself.
    """

    def validate(self):
        """An engine raphain cannot instantiate is not a usable engine."""
        self.validate_kind()
        self.validate_authentication()
        self.validate_documents()
        self.validate_default()

    def validate_kind(self):
        """raphain has a closed set of adapters; anything else is a typo."""
        if self.kind and self.kind not in SUPPORTED_KINDS:
            frappe.throw(
                _("{0} is not a supported engine kind. Use one of: {1}.").format(
                    self.kind, ", ".join(SUPPORTED_KINDS)
                )
            )

    def validate_authentication(self):
        """A secret is an environment reference or an inline value, never both."""
        auth_type = self.auth_type or "none"
        if auth_type not in AUTH_TYPES:
            frappe.throw(
                _("{0} is not a supported authentication type. Use one of: {1}.").format(
                    auth_type, ", ".join(AUTH_TYPES)
                )
            )
        if auth_type == "oauth":
            self.validate_oauth()
            return
        if any(self.get(fieldname) for fieldname in OAUTH_FIELDS):
            frappe.throw(
                _("OAuth credentials belong to an engine authenticated with oauth.")
            )
        given = [bool(self.auth_env), bool(self.auth_secret)]
        if auth_type not in SECRET_AUTH_TYPES:
            if any(given):
                frappe.throw(_("An engine without authentication carries no secret."))
            return
        if all(given):
            frappe.throw(
                _("Give {0} an environment reference or an inline secret, not both.").format(
                    self.engine_name or auth_type
                )
            )
        if not any(given):
            frappe.throw(
                _("{0} authentication needs an environment reference or an inline secret.").format(
                    auth_type
                )
            )

    def validate_oauth(self):
        """An OAuth engine carries tokens, in raphain's own oauth shape."""
        if self.auth_env or self.auth_secret:
            frappe.throw(_("An OAuth engine carries an access token, not an API key."))
        given = [bool(self.auth_access_token_env), bool(self.auth_access_token)]
        if all(given):
            frappe.throw(
                _("Give {0} an access token reference or an inline access token, not both.").format(
                    self.engine_name or "oauth"
                )
            )
        if not any(given):
            frappe.throw(
                _("OAuth authentication needs an access token environment reference or an inline access token.")
            )
        self.validate_oauth_refresh()

    def validate_oauth_refresh(self):
        """raphain errors at load time when a refresh block has nothing to exchange."""
        refresh = self.auth_refresh
        if refresh is None or refresh == "":
            return
        if isinstance(refresh, str):
            try:
                refresh = json.loads(refresh)
            except ValueError:
                frappe.throw(_("The OAuth refresh block is not valid JSON."))
        if not isinstance(refresh, dict):
            frappe.throw(_("The OAuth refresh block must be a JSON object."))
        self.auth_refresh = json.dumps(refresh)
        if not (self.auth_refresh_token_env or refresh.get("refresh_token")):
            frappe.throw(
                _(
                    "Automatic OAuth refresh needs a refresh token: give a refresh token"
                    " environment reference, or a refresh_token inside the refresh block."
                )
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
        """The JSON blocks must already hold the shape raphain reads them in."""
        for fieldname in OBJECT_FIELDS:
            document = self.parsed_document(fieldname)
            if document is None:
                continue
            if not isinstance(document, dict):
                frappe.throw(_("{0} must be a JSON object.").format(fieldname))
            self.set(fieldname, json.dumps(document))
        headers = self.parsed_document("headers")
        if headers is None:
            return
        if not isinstance(headers, list) or not all(
            isinstance(entry, dict) and entry.get("name") and entry.get("value")
            for entry in headers
        ):
            frappe.throw(_("Headers must be a list of objects with a name and a value."))
        self.set("headers", json.dumps(headers))

    def validate_default(self):
        """raphain names one default provider, so only one engine may claim it."""
        if not cint(self.is_default):
            return
        if not cint(self.enabled):
            self.is_default = 0
            return
        for other in frappe.get_all(
            "HD AI Engine",
            filters={"is_default": 1, "name": ("!=", self.name)},
            pluck="name",
        ):
            frappe.db.set_value("HD AI Engine", other, "is_default", 0)
