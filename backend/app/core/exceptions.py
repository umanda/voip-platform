class VoIPBaseError(Exception):
    """Base class for all platform-specific errors."""


class AccountNotFoundError(VoIPBaseError):
    """credit_code not found in credits_customers, or user is deleted."""


class AccountSuspendedError(VoIPBaseError):
    """credits_customers.is_blocked is true."""


class InsufficientCreditError(VoIPBaseError):
    """Available credit < minimum required for the call (R-BILL-03)."""


class CallAuthorizationError(VoIPBaseError):
    """General authorization failure not covered by the above."""


class DIDNotFoundError(VoIPBaseError):
    """Dialed number not found in site_ivr_numbers."""


class RoutingError(VoIPBaseError):
    """No gateway available for the destination."""


class RedisSessionNotFoundError(VoIPBaseError):
    """call:{uuid} key absent in Redis — session lost or expired."""


class CreditDeductionError(VoIPBaseError):
    """credit:{account_id} key absent in Redis — needs reconciliation."""
