"""One error envelope for REST and Socket.IO acknowledgements."""

from typing import Any

from pydantic import ValidationError


class AppError(Exception):
    def __init__(
        self, code: str, message: str, field: str | None = None, http_status: int = 400
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.http_status = http_status

    def body(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "field": self.field},
        }


def validation_error(exc: ValidationError | Any) -> AppError:
    first = exc.errors()[0]
    loc = [
        part for part in first.get("loc", ()) if part not in ("body", "query", "path")
    ]
    field = str(loc[0]) if loc else None
    return AppError("VALIDATION_ERROR", "Проверь формат и допустимые значения.", field)


def not_found() -> AppError:
    return AppError("CANVAS_NOT_FOUND", "Такого холста нет.", http_status=404)


def too_large() -> AppError:
    return AppError("PAYLOAD_TOO_LARGE", "Сообщение слишком большое.", http_status=413)
