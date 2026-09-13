from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import LeagueAnnouncement, LeagueMembership, UserAnnouncementDismissal


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


def league_announcements(request):
    """Active league announcements for the current user's memberships."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"league_announcements": []}

    now = timezone.now()
    membership_league_ids = LeagueMembership.objects.filter(
        user=user,
        is_active=True,
    ).values_list("league_id", flat=True)

    dismissed = UserAnnouncementDismissal.objects.filter(
        announcement_id=OuterRef("pk"),
        user=user,
    )

    announcements = (
        LeagueAnnouncement.objects.filter(
            league_id__in=membership_league_ids,
            is_active=True,
        )
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .annotate(is_dismissed=Exists(dismissed))
        .filter(
            Q(kind=LeagueAnnouncement.KIND_PERSISTENT)
            | Q(kind=LeagueAnnouncement.KIND_ONE_TIME, is_dismissed=False)
        )
        .select_related("league")
        .order_by("-created_at")
    )
    return {"league_announcements": list(announcements)}


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
