"""Errors as JSON with a stable code and a readable message:

    {"error": {"code": "PERMISSION_DENIED", "message": "..."}}

Service errors keep their own code. The API adds the ones that only exist at the HTTP
boundary (authentication, upload size).
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import (
    InvalidState,
    NotFound,
    PermissionDenied,
    ServiceError,
    ValidationError,
)

_STATUS_BY_SERVICE_ERROR = {
    PermissionDenied: 403,
    NotFound: 404,
    InvalidState: 409,
    ValidationError: 422,
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def unauthenticated(message: str = "Authentication is required.") -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", message)


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        status = next(
            (code for kind, code in _STATUS_BY_SERVICE_ERROR.items() if isinstance(exc, kind)), 400
        )
        return JSONResponse(_body(exc.code, str(exc)), status_code=status)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()[:5]
        )
        return JSONResponse(
            _body("VALIDATION_ERROR", f"The request is not valid ({problems})."), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)
