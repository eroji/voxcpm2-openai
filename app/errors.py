"""OpenAI-style API errors. Every /v1 and /api error uses this shape."""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        type: str = "invalid_request_error",
        param: str | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.type = type
        self.param = param
        self.code = code
        self.headers = headers or {}

    def body(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.code,
            }
        }


class BadRequest(APIError):
    def __init__(self, message: str, *, param: str | None = None, code: str | None = None):
        super().__init__(400, message, param=param, code=code)


class Unauthorized(APIError):
    def __init__(self, message: str = "Invalid API key"):
        super().__init__(
            401,
            message,
            type="authentication_error",
            code="invalid_api_key",
            headers={"WWW-Authenticate": "Bearer"},
        )


class Forbidden(APIError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(403, message, type="permission_error")


class NotFound(APIError):
    def __init__(self, message: str, *, code: str = "not_found"):
        super().__init__(404, message, code=code)


class QueueFull(APIError):
    def __init__(self, retry_after: int = 1):
        super().__init__(
            429,
            "The engine queue is full. Retry shortly.",
            type="rate_limit_error",
            code="queue_full",
            headers={"Retry-After": str(retry_after)},
        )


class EngineNotReady(APIError):
    def __init__(self, message: str = "Engine is warming up. Try again in a few seconds."):
        super().__init__(
            503,
            message,
            type="service_unavailable",
            code="engine_not_ready",
            headers={"Retry-After": "5"},
        )


class EngineError(APIError):
    def __init__(self, message: str = "Engine request failed"):
        super().__init__(502, message, type="upstream_error", code="engine_error")


class Conflicting(APIError):
    def __init__(self, message: str):
        super().__init__(409, message, code="conflict")
