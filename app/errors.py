"""One error envelope for REST and Socket.IO acknowledgements.

{"ok": false, "error": {"code", "message", "field", "retryable", "details"}}
The client renders its own text from code + details; message is a localized fallback.
"""

from typing import Any

from pydantic import ValidationError

from app.i18n import translate

# NOT_JOINED is socket-only: it tells the client to resubscribe before retrying.
STATUS = {
    "VALIDATION_FAILED": 400,
    "UNAUTHENTICATED": 401,
    "PAYMENT_REQUIRED": 402,
    "PAYMENT_FAILED": 402,
    "FORBIDDEN_ROLE": 403,
    "NOT_FOUND": 404,
    "SELECTION_BUSY": 409,
    "STROKES_ALREADY_ALIVE": 409,
    "ENTITY_LIMIT": 409,
    "CONTROL_TAKEN": 409,
    "NOT_JOINED": 409,
    "WORLD_LIMIT": 409,
    "CHOICE_EXPIRED": 410,
    "PAYLOAD_TOO_LARGE": 413,
    "SELECTION_EMPTY": 422,
    "SELECTION_TOO_LARGE": 422,
    "SELECTION_MULTIPLE": 422,
    "UNSAFE_CONTENT": 422,
    "CANVAS_FROZEN": 423,
    "QUOTA_EXCEEDED": 429,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
    "VLM_UNAVAILABLE": 503,
    "VLM_TIMEOUT": 504,
}
RETRYABLE = {
    "RATE_LIMITED",
    "INTERNAL",
    "NOT_JOINED",
    "SELECTION_BUSY",
    "VLM_TIMEOUT",
    "VLM_UNAVAILABLE",
}


class AppError(Exception):
    def __init__(
        self, code: str, field: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.details = details or {}
        self.http_status = STATUS[code]

    def body(self, locale: str = "en") -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": translate(locale, f"err.{self.code}", self.details),
                "field": self.field,
                "retryable": self.code in RETRYABLE,
                "details": self.details,
            },
        }


def validation_error(exc: ValidationError | Any) -> AppError:
    first = exc.errors()[0]
    loc = [
        part for part in first.get("loc", ()) if part not in ("body", "query", "path")
    ]
    return AppError("VALIDATION_FAILED", str(loc[0]) if loc else None)
