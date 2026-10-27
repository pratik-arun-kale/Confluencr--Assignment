"""Domain errors and the handlers that render every error in one consistent envelope:

    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_config import request_id_var

logger = logging.getLogger("app.errors")


class AppError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class AccountNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "ACCOUNT_NOT_FOUND"

    def __init__(self, account_id: object) -> None:
        super().__init__(f"Account {account_id} not found", {"account_id": str(account_id)})


class InsufficientBalanceError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "INSUFFICIENT_BALANCE"


class BalanceLimitExceededError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "BALANCE_LIMIT_EXCEEDED"


class DuplicateTransactionError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_TRANSACTION"


class DuplicateTransferError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "DUPLICATE_TRANSFER"


class ReferenceIdConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "REFERENCE_ID_CONFLICT"


class SameAccountTransferError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "SAME_ACCOUNT_TRANSFER"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id_var.get(),
        }
    }


def _field_name(loc: tuple[Any, ...]) -> str:
    # ("body", "amount") -> "amount"; ("query", "page") -> "page"
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts) or str(loc[0] if loc else "request")


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(
        status_code=exc.status_code, content=error_body(exc.code, exc.message, exc.details)
    )


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()

    if any(e.get("type") == "json_invalid" for e in errors):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error_body("INVALID_JSON", "Request body is not valid JSON"),
        )

    if any(e["loc"][:2] == ("path", "account_id") for e in errors):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error_body("INVALID_ACCOUNT_ID", "Account ID must be a valid UUID"),
        )

    details = [
        {"field": _field_name(tuple(e["loc"])), "message": e["msg"], "type": e["type"]}
        for e in errors
    ]
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=error_body("VALIDATION_ERROR", "Request validation failed", details),
    )


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    code = codes.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )


async def _unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_body("INTERNAL_ERROR", "An unexpected error occurred"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
