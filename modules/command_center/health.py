"""Allowlisted diagnostics. Never serialize exception messages or credentials."""
from integrations.google_auth import GoogleAuthError

AUTH_CODES = frozenset({
    "missing_token", "malformed_token", "missing_refresh_token",
    "reauthorization_required", "refresh_rejected", "refresh_unavailable",
    "invalid_refreshed_token",
})
AUTH_SOURCES = frozenset({"GOOGLE_TOKEN", "token.json"})


def safe_auth_diagnostic(exc: Exception) -> dict:
    if not isinstance(exc, GoogleAuthError):
        return {}
    return {
        "auth_error_code": exc.code if exc.code in AUTH_CODES else "unknown_auth_error",
        "auth_source": exc.source if exc.source in AUTH_SOURCES else "unknown",
    }


STATUS_TEXT = (
    "Webhook відповідає. Це не підтверджує роботу збору, реєстру, Hub чи доставки. "
    "Наскрізний стан потрібно перевіряти за останніми звітами виконання."
)
BRIEFING_TEXT = (
    "Фоновий збір формує передачу Секретарю, а не готовий управлінський брифінг. "
    "Час і доставку останнього брифінгу цей webhook не перевіряє."
)
