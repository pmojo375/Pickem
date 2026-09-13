from django.conf import settings

from .models import LeagueMembership


def league_permissions(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"can_manage_league": False}
    if user.is_staff:
        return {"can_manage_league": True}
    return {
        "can_manage_league": LeagueMembership.objects.filter(
            user=user,
            is_active=True,
            role__in=("owner", "admin"),
        ).exists()
    }


def posthog(request):
    """Expose PostHog config + identify payload for the base template snippet."""
    enabled = bool(getattr(settings, "POSTHOG_ENABLED", False))
    ctx = {
        "posthog_enabled": enabled,
        "posthog_api_key": getattr(settings, "POSTHOG_API_KEY", ""),
        "posthog_host": getattr(settings, "POSTHOG_HOST", "https://us.i.posthog.com"),
    }
    if not enabled:
        return ctx

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return ctx

    active_memberships = LeagueMembership.objects.filter(user=user, is_active=True)
    ctx["posthog_person"] = {
        "distinct_id": str(user.pk),
        "username": user.username,
        "email": user.email or "",
        "name": user.get_full_name() or user.username,
        "is_staff": user.is_staff,
        "date_joined": user.date_joined.isoformat(),
        "league_count": active_memberships.count(),
        "is_league_admin": active_memberships.filter(
            role__in=("owner", "admin")
        ).exists(),
    }
    return ctx
