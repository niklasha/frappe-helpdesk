from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk.api.settings.email import create_email_account


class TestEmailAccountSettings(FrappeTestCase):
    def test_custom_account_uses_optional_login_id(self):
        """A custom mailbox can authenticate with a username distinct from its address."""
        account_name = "Custom IMAP Login Test"

        with patch(
            "frappe.email.doctype.email_account.email_account.EmailAccount.get_incoming_server"
        ):
            name = create_email_account(
                {
                    "email_account_name": account_name,
                    "email_id": "support@example.com",
                    "login_id": "support-imap-user",
                    "service": "Custom",
                    "password": "test-password",
                    "email_server": "imap.example.com",
                    "incoming_port": 993,
                    "smtp_server": "smtp.example.com",
                    "smtp_port": 587,
                }
            )

        self.addCleanup(frappe.delete_doc, "Email Account", name, force=True)
        account = frappe.get_doc("Email Account", name)
        self.assertEqual(account.login_id, "support-imap-user")
        self.assertTrue(account.login_id_is_different)

    def test_custom_account_without_login_id_uses_email_id(self):
        """An empty override keeps Frappe's normal email-address login fallback."""
        account_name = "Custom IMAP Default Login Test"

        with patch(
            "frappe.email.doctype.email_account.email_account.EmailAccount.get_incoming_server"
        ):
            name = create_email_account(
                {
                    "email_account_name": account_name,
                    "email_id": "support-default@example.com",
                    "login_id": "   ",
                    "service": "Custom",
                    "password": "test-password",
                    "email_server": "imap.example.com",
                    "incoming_port": 993,
                    "smtp_server": "smtp.example.com",
                    "smtp_port": 587,
                }
            )

        self.addCleanup(frappe.delete_doc, "Email Account", name, force=True)
        account = frappe.get_doc("Email Account", name)
        self.assertFalse(account.login_id)
        self.assertFalse(account.login_id_is_different)
