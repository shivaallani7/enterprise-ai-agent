"""
Retry policy for Azure OpenAI transient errors.

Azure OpenAI can return:
  429  — rate limit / TPM quota
  503  — service temporarily unavailable
  httpx.TimeoutException — network timeout

We use tenacity with exponential back-off + jitter so concurrent callers
don't all retry at the same instant.
"""
from __future__ import annotations

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
    RetryCallState,
)

logger = structlog.get_logger()

# Azure status codes that are safe to retry
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    # openai SDK wraps errors; check for rate-limit / server error types
    cls_name = type(exc).__name__
    return cls_name in {
        "RateLimitError",
        "APITimeoutError",
        "InternalServerError",
        "APIConnectionError",
        "ServiceUnavailableError",
    }


def _log_retry(state: RetryCallState) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        "Azure OpenAI transient error — retrying",
        attempt=state.attempt_number,
        wait_seconds=round(state.next_action.sleep if state.next_action else 0, 1),
        error=str(exc)[:120],
    )


def azure_retry() -> AsyncRetrying:
    """
    Returns a configured AsyncRetrying context manager.

    Usage:
        async for attempt in azure_retry():
            with attempt:
                result = await some_azure_call()
    """
    return AsyncRetrying(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
        before_sleep=_log_retry,
        reraise=True,
    )
