"""Thin LiteLLM wrapper — one request/response passthrough to Gemini.

No retries, no streaming, no tool-calling loop (that's the agent's job).
"""

import litellm

from app.config import get_settings


class LLMError(Exception):
    """Raised when the LLM call fails, so raw litellm stack traces never
    surface through the API."""


def get_completion(
    messages: list[dict], tools: list[dict] | None = None
) -> litellm.ModelResponse:
    settings = get_settings()
    try:
        return litellm.completion(
            model=settings.model_name,
            messages=messages,
            tools=tools,
            # Passed explicitly (not via env auto-detection) so the source of
            # the credential is obvious.
            api_key=settings.gemini_api_key,
        )
    except Exception as exc:
        raise LLMError(
            f"LLM call to '{settings.model_name}' failed: {type(exc).__name__}: {exc}"
        ) from exc
