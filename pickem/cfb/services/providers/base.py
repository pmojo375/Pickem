"""Bounded, cached provider transport. No automatic retries consume quota."""
import hashlib
import json

import requests
from django.conf import settings
from django.core.cache import cache


class ProviderError(ValueError):
    pass


class QuotaClient:
    def __init__(self, base_url, namespace, api_key=None, max_requests=3, force=False):
        self.base_url = base_url
        self.namespace = namespace
        self.api_key = settings.CFBD_API_KEY if api_key is None else api_key
        self.max_requests = max_requests
        self.requests_used = 0
        self.force = force
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})
        # Football and basketball share a quota and therefore a cooldown.
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        self.cooldown_key = f"college-data:cooldown:{key_hash}"

    def get(self, endpoint, params, ttl=3600):
        if not self.api_key:
            raise ProviderError("Configure CFBD_API_KEY; basketball uses the same key.")
        key = hashlib.sha256(json.dumps([self.namespace, endpoint, params], sort_keys=True).encode()).hexdigest()
        cache_key = f"sports-provider:{key}"
        cached = cache.get(cache_key)
        if cached is not None and not self.force:
            return cached
        if cache.get(self.cooldown_key):
            raise ProviderError("Provider is cooling down after a rate limit; no request sent.")
        if self.requests_used >= self.max_requests:
            raise ProviderError(f"Request budget of {self.max_requests} reached; no further request sent.")
        self.requests_used += 1
        try:
            response = self.session.get(self.base_url + endpoint, params=params, timeout=30)
            if response.status_code == 429:
                try:
                    retry_after = max(60, int(response.headers.get("Retry-After", 3600)))
                except (ValueError, TypeError):
                    retry_after = 3600
                cache.set(self.cooldown_key, True, timeout=retry_after)
                raise ProviderError("Provider rate limit reached; stopped without retrying.")
            if response.status_code in (401, 403):
                raise ProviderError("Provider rejected access; check CFBD_API_KEY and subscription access.")
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Provider request failed; no automatic retry was made.") from exc
        if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
            raise ProviderError("Unexpected provider response: expected a list of objects.")
        cache.set(cache_key, data, timeout=ttl)
        return data
