"""Fill the reference data on sites created before seeding reached the install.

Every site created with `bench new-site` before Wave 11 has the earlier seed
patches recorded in its Patch Log without their rows behind them. This patch is
how those sites get the data; `after_install` is how new ones do.
"""

from helpdesk.setup.seed import seed_reference_data


def execute():
    seed_reference_data()
