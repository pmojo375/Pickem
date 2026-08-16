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
