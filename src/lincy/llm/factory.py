"""LLM client factory.

Provider-agnostic: delegates client creation to config.create_client().
Only handles shared concerns (timeout override, retry wrapping).
No provider-specific imports or logic.
"""

from ..core.schema import LLMConfig
from .base import LLMClient
from .retry import with_llm_retry


def _apply_request_timeout(
    config: LLMConfig,
    request_timeout: float | None,
) -> LLMConfig:
    if request_timeout is None:
        return config
    return config.model_copy(update={"request_timeout": request_timeout})


def create_client(
    config: LLMConfig,
    transient_retries: int = 0,
    request_timeout: float | None = None,
    rate_limit_retries: int = 0,
    retry_label: str | None = None,
    **provider_kwargs,
) -> LLMClient:
    """Create LLM client via provider config's create_client() method.

    provider_kwargs are forwarded to the config's create_client().
    Each provider declares which kwargs it accepts; unsupported kwargs
    raise TypeError (no silent ignore).
    """
    config = _apply_request_timeout(config, request_timeout)
    client = config.create_client(**provider_kwargs)
    return with_llm_retry(
        client,
        transient_retries,
        rate_limit_retries,
        label=retry_label,
    )
