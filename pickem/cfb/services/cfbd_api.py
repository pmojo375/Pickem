"""Compatibility import for the football provider adapter."""
from .providers.football import CFBDAPIClient, get_cfbd_client

__all__ = ["CFBDAPIClient", "get_cfbd_client"]
