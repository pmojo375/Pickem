"""Explicit adapter registry: adding a sport does not assume its API schema."""
from .base import ProviderError
from .basketball import BasketballProvider

PROVIDERS = {"cbbd": BasketballProvider}


def get_provider(sport, **kwargs):
    factory = PROVIDERS.get(sport.data_provider)
    if factory is None:
        raise ProviderError(f"No import adapter is registered for {sport.name}.")
    return factory(**kwargs)
