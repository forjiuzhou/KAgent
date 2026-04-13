"""Retry with exponential backoff for LLM API calls.

Handles transient errors that are common in production:
- Rate limiting (HTTP 429)
- Server overload (HTTP 529 for Anthropic, 5xx for OpenAI)
- Network timeouts and connection errors

Non-retryable errors (auth failures, bad requests) are raised immediately.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from noteweaver.constants import (
    MAX_RETRIES,
    INITIAL_BACKOFF,
    BACKOFF_MULTIPLIER,
    MAX_BACKOFF,
    RETRYABLE_STATUS_CODES as _RETRYABLE_STATUS_CODES,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

# Exception class names that indicate retryable conditions
_RETRYABLE_ERROR_NAMES = {
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "ServiceUnavailableError",
    "OverloadedError",
    "ConnectionError",
    "TimeoutError",
}


def _is_retryable(exc: Exception) -> bool:
    """Determine if an exception is worth retrying."""
    exc_name = type(exc).__name__
    if exc_name in _RETRYABLE_ERROR_NAMES:
        return True

    # Check for status_code attribute (openai and anthropic both expose this)
    status = getattr(exc, "status_code", None)
    if status and status in _RETRYABLE_STATUS_CODES:
        return True

    # httpx-level errors
    if "timeout" in exc_name.lower() or "connection" in exc_name.lower():
        return True

    return False


def _extract_retry_after(exc: Exception) -> float | None:
    """Try to extract Retry-After hint from the exception/response."""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", {})
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass
    return None


def with_retry(
    fn: Callable[..., T],
    *args,
    max_retries: int = MAX_RETRIES,
    initial_backoff: float = INITIAL_BACKOFF,
    **kwargs,
) -> T:
    """Call *fn* with retry on transient failures.

    Uses exponential backoff with optional Retry-After header respect.
    Non-retryable errors are raised immediately.
    """
    backoff = initial_backoff
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc

            if not _is_retryable(exc):
                raise

            if attempt >= max_retries:
                fn_name = getattr(fn, "__name__", repr(fn))
                log.warning(
                    "All %d retries exhausted for %s: %s",
                    max_retries, fn_name, exc,
                )
                raise

            # Respect Retry-After header if present
            wait = _extract_retry_after(exc) or backoff
            wait = min(wait, MAX_BACKOFF)

            log.info(
                "Retryable error (attempt %d/%d): %s. Waiting %.1fs...",
                attempt + 1, max_retries, type(exc).__name__, wait,
            )
            time.sleep(wait)
            backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)

    # Should never reach here, but satisfy type checker
    raise last_exc  # type: ignore[misc]
