from typing import Any

from app.schemas import ErrorResponse

_DESCRIPTIONS = {
    400: "Validation error (VALIDATION_ERROR, INVALID_JSON, INVALID_ACCOUNT_ID, ...)",
    404: "Account not found (ACCOUNT_NOT_FOUND)",
    409: "Duplicate reference_id (DUPLICATE_* or REFERENCE_ID_CONFLICT)",
    422: "Business rule violated (INSUFFICIENT_BALANCE, BALANCE_LIMIT_EXCEEDED)",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses` entries documenting the shared error envelope."""
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in status_codes
    }
