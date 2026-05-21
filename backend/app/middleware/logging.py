"""
Structured request/response logging middleware.

Logs every request as a single JSON line with:
  method, path, status, duration_ms, request_id, user_sub

The request_id is taken from the incoming X-Request-ID header if present,
otherwise generated. It is echoed back in the response so clients and
distributed traces can correlate logs end-to-end.
"""
from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


def _extract_user_sub(authorization: str) -> str:
    """
    Best-effort sub extraction from JWT without full verification.
    Used only for logging — auth is still enforced by deps.get_current_user.
    """
    if not authorization.startswith("Bearer "):
        return "anonymous"
    token = authorization[7:]
    try:
        # JWT is three base64url segments; decode the payload (middle) without verification
        import base64, json
        payload_b64 = token.split(".")[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
        return claims.get("sub", "unknown")
    except Exception:
        return "unknown"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        user_sub = _extract_user_sub(request.headers.get("authorization", ""))

        # Bind request context to all log calls within this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            user_sub=user_sub,
        )

        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            logger.error(
                "Request failed with unhandled exception",
                duration_ms=duration_ms,
                error=str(exc),
                exc_info=True,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "Request completed",
            status=response.status_code,
            duration_ms=duration_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
