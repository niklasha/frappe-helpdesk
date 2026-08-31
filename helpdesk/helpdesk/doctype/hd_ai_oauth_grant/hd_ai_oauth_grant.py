from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime

# How long an authorization the administrator never finished stays redeemable. A
# code and state pair sits in a browser history, a proxy log or a chat message,
# so the grant that would honour it has to die on its own.
GRANT_LIFETIME_MINUTES = 10


class HDAIOAuthGrant(Document):
    """One authorization in flight, and nothing that outlives it.

    The row holds the server's half of the exchange — the hashed state, the PKCE
    verifier, the device code — for as long as the grant is pending. Once the
    grant is finished those halves are cleared: a spent proof kept anyway is a
    second copy of a credential, in a table nobody thinks of as one.
    """

    def validate(self):
        """A grant with no absolute expiry never stops being redeemable."""
        if not self.expires_at:
            self.expires_at = add_to_date(now_datetime(), minutes=GRANT_LIFETIME_MINUTES)
