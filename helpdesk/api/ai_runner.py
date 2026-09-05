"""Helpdesk's call into the raphain-embedding runner.

raphain is a Rust library crate, so the provider access lives in the runner
service that embeds it. This module owns the one narrow contract between the
two: Helpdesk posts an engine name and the messages to it, and takes back the
text the runner's provider wrote. No provider is spoken to from here.
"""

import frappe
import requests
from frappe import _
from frappe.utils import cint

from helpdesk.utils import agent_only, is_admin

RUNNER_SETTINGS = "HD AI Runner Settings"
GENERATE_PATH = "/v1/generate"
DEFAULT_REQUEST_TIMEOUT = 30
# What a runner answers when it does not understand the request body. A
# runner from before Wave 20 rejects a content list (parts) this way; the
# caller may then try again with the text alone.
REJECTED_STATUSES = (400, 422)


class RunnerRejected(frappe.ValidationError):
    """The runner refused the request as malformed (HTTP 400 or 422)."""


def _settings() -> "frappe.Document":
    """Read the runner configuration as plain values."""
    doc = frappe.get_single(RUNNER_SETTINGS)
    return {
        "enabled": cint(doc.enabled),
        "runner_url": doc.runner_url,
        "request_timeout": cint(doc.request_timeout) or DEFAULT_REQUEST_TIMEOUT,
    }


@frappe.whitelist()
@agent_only
def runner_settings() -> dict:
    """Return how Helpdesk reaches the AI runner."""
    return _settings()


@frappe.whitelist(methods=["POST"])
@agent_only
def configure_runner(
    runner_url: str | None = None,
    enabled: int | bool | None = None,
    request_timeout: int | None = None,
) -> dict:
    """Point Helpdesk at a runner, and return the settings as they now stand."""
    if not is_admin():
        frappe.throw(
            _("Only an administrator may configure the AI runner."),
            frappe.PermissionError,
        )
    doc = frappe.get_single(RUNNER_SETTINGS)
    if runner_url is not None:
        doc.runner_url = runner_url
    if enabled is not None:
        doc.enabled = cint(enabled)
    if request_timeout is not None:
        doc.request_timeout = cint(request_timeout)
    doc.save(ignore_permissions=True)
    return _settings()


def is_runner_available() -> bool:
    """True when a runner is switched on and its address is known."""
    settings = _settings()
    return bool(settings["enabled"] and settings["runner_url"])


def generate(
    engine: str,
    messages: list | str,
    parameters: dict | str | None = None,
) -> dict:
    """Have the runner generate text with one engine, and return its response.

    The runner resolves the provider behind the engine from its own raphain
    registry, so what travels is only the engine's name and the conversation.

    A message's content is a string, or (Wave 20) a list of parts —
    `{"type": "text", "text"}` and `{"type": "image", "data_url"}` with a
    base64 data URL — when a picture is shown beside the text. The list goes
    to the runner as built; the string form is unchanged.
    """
    settings = _settings()
    if not (settings["enabled"] and settings["runner_url"]):
        frappe.throw(_("No AI runner is configured."))
    payload = {"engine": engine, "messages": messages, "parameters": parameters or {}}
    url = settings["runner_url"].rstrip("/") + GENERATE_PATH
    try:
        response = requests.post(
            url, json=payload, timeout=settings["request_timeout"]
        )
    except requests.RequestException as exception:
        frappe.throw(_("The AI runner could not be reached: {0}").format(exception))
    if response.status_code != 200:
        frappe.throw(
            _("The AI runner answered with status {0}.").format(response.status_code),
            exc=RunnerRejected if response.status_code in REJECTED_STATUSES else frappe.ValidationError,
        )
    try:
        result = response.json()
    except ValueError:
        frappe.throw(_("The AI runner did not answer with JSON."))
    if not result.get("text"):
        frappe.throw(_("The AI runner returned no text."))
    return result
