"""Sport/season resolution shared by the UI, APIs, and provider integrations."""
from cfb.models import DEFAULT_SPORT, Season


def get_active_season(sport=DEFAULT_SPORT):
    sport_id = getattr(sport, "pk", sport)
    return Season.objects.filter(sport_id=sport_id, is_active=True).select_related("sport").first()


def game_cache_key(game):
    # Keep the football key compatible with existing cached payloads.
    from django.conf import settings

    external_id = game.provider_ids.get("espn") or game.external_id
    namespace = "" if game.season.sport_id == DEFAULT_SPORT else f"{game.season.sport_id}:"
    return f"{settings.REDIS_KEY_GAME_PREFIX}{namespace}{external_id}"
