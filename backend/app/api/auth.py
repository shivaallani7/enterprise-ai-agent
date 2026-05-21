"""
POST /api/auth/login  — dev login with username + password
POST /api/auth/logout — clear session (informational; token is stateless)

In dev mode, credentials are validated against DEV_USERS below.
Passwords are stored as bcrypt hashes; the seed script below generates them.
For now, username == password is acceptable for internal testing.

The returned token is "dev-<username>" which the existing Bearer auth
bypass in deps.py accepts when APP_ENVIRONMENT=development.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.limiter import limiter

settings = get_settings()
router = APIRouter(tags=["auth"])


# ── Seeded dev users ──────────────────────────────────────────────────────────
# password_hash = sha256(password) — simple, sufficient for internal dev use.
# To add users: add an entry here with sha256(password).hex().

def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


DEV_USERS: dict[str, dict] = {
    "shivaallani": {
        "password_hash": _hash("shivaallani"),
        "display_name":  "Shiva Allani",
        "default_persona": "qa_engineer",
    },
    "shivaallani5": {
        "password_hash": _hash("shivaallani5"),
        "display_name":  "Shiva Allani 5",
        "default_persona": "software_engineer",
    },
    "shivaallani7": {
        "password_hash": _hash("shivaallani7"),
        "display_name":  "Shiva Allani 7",
        "default_persona": "product_owner",
    },
    "bindu": {
        "password_hash": _hash("bindu"),
        "display_name":  "Bindu",
        "default_persona": "general",
    },
}


# ── Request / response models ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    display_name: str
    default_persona: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/auth/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest):
    username = body.username.strip().lower()
    user = DEV_USERS.get(username)

    if not user or user["password_hash"] != _hash(body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return LoginResponse(
        token=f"dev-{username}",
        username=username,
        display_name=user["display_name"],
        default_persona=user["default_persona"],
    )


@router.post("/auth/logout")
async def logout():
    # Token is stateless — client simply drops it from localStorage.
    return {"ok": True}
