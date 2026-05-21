"""
FastAPI application entry point.

Startup order:
  1. Load .env into os.environ (must happen before LangChain/LangGraph imports
     so LangSmith tracing env vars are available when the SDK initializes)
  2. Configure structlog (JSON in prod, console in dev)
  3. Connect Application Insights via OpenTelemetry auto-instrumentation
  4. Add middlewares: security headers → request logging → CORS
     (outermost first — CORS must be before logging so preflight OPTIONS
     are still logged, security headers are outermost so they apply to
     every response including error responses)
  5. Add rate limiter exception handler
  6. Mount routers
"""
from __future__ import annotations

# ── Load .env into os.environ FIRST ──────────────────────────────────────────
# LangSmith reads LANGCHAIN_TRACING_V2 / LANGCHAIN_API_KEY from os.environ at
# import time. pydantic-settings only loads .env into the Settings object, not
# into os.environ, so we must call load_dotenv() explicitly before any
# LangChain / LangGraph imports happen.
from dotenv import load_dotenv
load_dotenv()

# ── Load secrets from Azure Key Vault (production only) ───────────────────────
# MUST run before any app module is imported, because modules like supervisor.py
# call get_settings() at module level. By loading KV secrets into os.environ
# here, pydantic-settings will pick them up when Settings() first constructs.
import os as _os
_kv_url = _os.environ.get("KEY_VAULT_URL", "")
if _kv_url:
    try:
        from azure.identity import ManagedIdentityCredential
        from azure.keyvault.secrets import SecretClient
        _credential = ManagedIdentityCredential()
        _kv_client = SecretClient(vault_url=_kv_url, credential=_credential)
        for _secret in _kv_client.list_properties_of_secrets():
            _env_name = _secret.name.upper().replace("-", "_")
            if _env_name not in _os.environ:
                _os.environ[_env_name] = _kv_client.get_secret(_secret.name).value
        print(f"Loaded secrets from Key Vault: {_kv_url}")
    except Exception as _kv_exc:
        print(f"WARNING: Key Vault load failed: {_kv_exc}")
# ─────────────────────────────────────────────────────────────────────────────

import json
import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import get_settings
from app.api.chat import router as chat_router
from app.api.feedback import router as feedback_router
from app.api.copilot import router as copilot_router
from app.api.jira import router as jira_router
from app.api.users import router as users_router
from app.api.auth import router as auth_router
from app.api.ingest import router as ingest_router
from app.limiter import limiter
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.security import SecurityHeadersMiddleware

settings = get_settings()


# ── Logging configuration ─────────────────────────────────────────────────────

def _configure_logging() -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.app_environment == "production":
        # JSON lines for App Insights / Log Analytics ingestion
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper(), logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper(), logging.DEBUG)
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )


_configure_logging()
logger = structlog.get_logger()


# ── Rate limiter ──────────────────────────────────────────────────────────────
# Defined in app.limiter to avoid circular import (main imports routers,
# routers import limiter). See app/limiter.py for the key function logic.
# In-memory per-process. For multi-instance deployments without sticky
# sessions, switch to Redis: Limiter(key_func=..., storage_uri="redis://...")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Application Insights — OpenTelemetry auto-instruments FastAPI HTTP
    # requests, giving distributed traces in Azure Monitor automatically.
    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            configure_azure_monitor(
                connection_string=settings.applicationinsights_connection_string
            )
            logger.info("Application Insights connected")
        except Exception as exc:
            logger.warning("Application Insights setup failed", error=str(exc))

    logger.info(
        "Enterprise AI Agent starting",
        env=settings.app_environment,
        log_level=settings.log_level,
    )
    yield
    logger.info("Enterprise AI Agent shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Enterprise AI Agent API",
    version="1.0.0",
    lifespan=lifespan,
    # Disable default exception detail in production responses
    docs_url="/docs" if settings.app_environment != "production" else None,
    redoc_url="/redoc" if settings.app_environment != "production" else None,
)

# Attach rate limiter state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middlewares — added in reverse execution order (last added = outermost)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ── Global exception handler ──────────────────────────────────────────────────
# Catches anything that slips past route-level handlers.
# Returns a generic 500 without leaking internal details in production.

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception",
        path=str(request.url.path),
        error=str(exc),
        exc_info=True,
    )
    if settings.app_environment == "production":
        detail = "An unexpected error occurred. Please try again."
    else:
        detail = str(exc)
    return JSONResponse(status_code=500, content={"detail": detail})


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(chat_router, prefix="/api")
app.include_router(feedback_router, prefix="/api")
app.include_router(copilot_router, prefix="/api")
app.include_router(jira_router, prefix="/api")
app.include_router(users_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(ingest_router, prefix="/api")


@app.get("/health", tags=["ops"])
async def health():
    return {
        "status": "ok",
        "env": settings.app_environment,
        "version": "1.0.0",
    }
