"""Reference data a Helpdesk site cannot work without.

Seeding used to live only in patches, and that quietly failed the case it
mattered most in. `bench new-site` writes every patch that exists at install
time into the Patch Log and never runs it — sound for a schema patch, because
the fresh schema already has what the patch would have added, and wrong for a
patch that seeds data, because nothing else creates it. So an upgraded site had
its prompt library and its language catalogue, and a newly created one had
neither, with the patches recorded as applied.

The two entry points here are deliberately both kept. `after_install` covers
every site created from now on; the Wave 11 patch covers every site created
before. Neither makes the other redundant: on the next new site that patch will
itself be marked applied without running, which is precisely the defect it
exists to repair.

Every seed is idempotent by name and never touches a row that already exists,
so an administrator's tuning survives each migrate.
"""

import frappe

# The patch modules stay the home of each wave's data — that is where it was
# introduced and where its history reads. This module only guarantees the data
# is reachable outside the patch runner.
SEEDS = (
    ("helpdesk.patches.seed_wave1_statuses", "HD Ticket Status"),
    ("helpdesk.patches.seed_wave4_languages", "HD Supported Language"),
    ("helpdesk.patches.seed_wave5_roles", "Role"),
    ("helpdesk.patches.seed_wave7_prompts", "HD AI Prompt"),
    ("helpdesk.patches.seed_wave9_prompts", "HD AI Prompt"),
    ("helpdesk.patches.seed_wave10_oauth_providers", "HD AI OAuth Provider"),
)


def seed_reference_data() -> dict:
    """Run every seed, and report what each doctype holds afterwards.

    A seed that fails is reported rather than raised: a missing catalogue
    degrades one feature, while an install that aborts halfway leaves a site
    nobody can use.
    """
    seeded: dict[str, object] = {}
    for module_name, _doctype in SEEDS:
        try:
            frappe.get_attr(f"{module_name}.execute")()
        except Exception:
            frappe.log_error(
                title="Helpdesk reference data",
                message=f"{module_name} could not be seeded",
            )
            seeded.setdefault("failed", []).append(module_name)
    for _module_name, doctype in SEEDS:
        seeded[doctype] = frappe.db.count(doctype)
    return seeded
