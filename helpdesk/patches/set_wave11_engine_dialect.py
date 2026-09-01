"""Free the engines Wave 10 locked out by teaching their providers a dialect.

`set_wave10_provider_engine_kind` put engine_kind on the seeded providers. In
that moment every engine already bound to one and still saying "openai" became
unsaveable: the controller refused the disagreement, the settings page's Kind
field is locked and so offered no cure, and the OAuth grant saves the engine as
its last step — so re-connecting, the one action that would repair such an
engine, refused too. On frappe-demo that left a live connection unable to renew
and unable to be renewed by hand.

The controller now corrects an inherited disagreement rather than refusing it,
but these rows cannot wait for a save that nobody can perform. Only engines
whose own provider states a dialect are touched; an engine with no provider has
nothing to be wrong about.
"""

import frappe


def execute():
    for engine in frappe.get_all(
        "HD AI Engine",
        filters={"auth_oauth_provider": ["is", "set"]},
        fields=["name", "kind", "auth_oauth_provider"],
    ):
        dialect = frappe.db.get_value(
            "HD AI OAuth Provider", engine.auth_oauth_provider, "engine_kind"
        )
        if dialect and engine.kind != dialect:
            frappe.db.set_value("HD AI Engine", engine.name, "kind", dialect)
