"""Thin PostHog helpers so views/signals stay consistent."""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def posthog_enabled() -> bool:
    return bool(getattr(settings, "POSTHOG_ENABLED", False))


def capture(distinct_id, event: str, properties=None) -> None:
    if not posthog_enabled() or not distinct_id:
        return
    try:
        import posthog

        posthog.capture(str(distinct_id), event, properties=properties or {})
    except Exception:
        logger.exception("PostHog capture failed for %s", event)


def identify_request_user(user) -> None:
    if not posthog_enabled() or user is None:
        return
    try:
        from posthog import identify_context

        identify_context(str(user.pk))
    except Exception:
        logger.exception("PostHog identify_context failed")


def person_props(user) -> dict:
    return {
        "username": user.username,
        "email": user.email or "",
        "name": user.get_full_name() or user.username,
        "is_staff": user.is_staff,
        "date_joined": user.date_joined.isoformat(),
    }
