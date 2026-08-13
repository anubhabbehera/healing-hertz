class UnifiError(Exception):
    """Base error for UniFi API failures."""


class UnifiConnectionError(UnifiError):
    """Could not reach the console (DNS, TLS, refused, timeout)."""


class UnifiAuthError(UnifiError):
    """API key rejected (401/403)."""


class UnifiRateLimited(UnifiError):
    """429 persisted after retries."""
