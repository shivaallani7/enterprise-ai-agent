"""
Shared rate limiter instance.

Defined here (not in main.py) so routers can import it without creating a
circular dependency: main.py imports routers, routers need the limiter.
"""
from __future__ import annotations

import base64
import json

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _rate_limit_key(request: Request) -> str:
    """
    Use the authenticated user's sub claim as the rate limit key so that
    limits are per-user, not per-IP. Falls back to remote IP if there is
    no recognisable JWT (e.g. unauthenticated requests).
    """
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            sub = claims.get("sub", "")
            if sub:
                return sub
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
