import copy
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from django.db import connections, transaction

from cfb.models import DEFAULT_SPORT, League, LeagueRules, Season, Sport

logger = logging.getLogger(__name__)

# season_end_period is season-specific (points at a SchedulePeriod in the old season).
RULES_SKIP_FIELDS = {
    "id",
    "pk",
    "season",
    "season_id",
    "season_end_period",
    "season_end_period_id",
    "created_at",
    "updated_at",
}


class SeasonAlreadyExistsError(ValueError):
    """Raised when attempting to start a season year that already exists."""


def _clone_league_rules(previous_season: Season, new_season: Season) -> int:
    """Copy every LeagueRules row from the previous season onto the new season."""
    copied = 0
    for rules in LeagueRules.objects.filter(season=previous_season, league__sport_id=new_season.sport_id):
        data: Dict[str, Any] = {}
        for field in rules._meta.concrete_fields:
            if field.primary_key or field.name in RULES_SKIP_FIELDS or field.attname in RULES_SKIP_FIELDS:
                continue
            value = getattr(rules, field.attname)
            if isinstance(value, (dict, list)):
                value = copy.deepcopy(value)
            data[field.attname] = value
        data["season_id"] = new_season.id
        LeagueRules.objects.create(**data)
        copied += 1
    return copied


def _run_initialize_in_thread(year: int) -> None:
    """Run CFBD season init in-process when no Celery worker is available."""
    try:
        from cfb.tasks import initialize_season

        initialize_season(year)
    except Exception:
        logger.exception("Background initialize_season failed for %s", year)
    finally:
        connections.close_all()


def _start_initialize(year: int) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Start CFBD initialization.

    Prefers a live Celery worker. If none is reachable, runs the same task in a
    background thread so season creation still pulls calendar/teams/games.

    Returns (started, mode, error) where mode is "queued" or "background".
    """
    from cfb.tasks import initialize_season

    try:
        inspect = initialize_season.app.control.inspect(timeout=1.0)
        if inspect and inspect.ping():
            initialize_season.delay(year)
            logger.info("Queued initialize_season for %s on Celery", year)
            return True, "queued", None
    except Exception as exc:
        logger.warning("Celery unavailable for initialize_season %s: %s", year, exc)

    thread = threading.Thread(
        target=_run_initialize_in_thread,
        args=(year,),
        daemon=True,
        name=f"initialize-season-{year}",
    )
    thread.start()
    logger.info("Started initialize_season for %s in a background thread", year)
    return True, "background", None


def start_new_season(year: int, name: str = "", sport=DEFAULT_SPORT) -> Dict[str, Any]:
    """
    Rotate only the selected sport: create its season and copy its league rules.
    Start the legacy CFBD import only for college football; other providers are explicit.

    Returns a dict with season, previous_season, rules_copied, initialize_queued,
    initialize_mode, and initialize_error.
    """
    sport = Sport.objects.get(pk=getattr(sport, "pk", sport))
    season_name = (name or "").strip() or f"{year} Season"

    with transaction.atomic():
        Sport.objects.select_for_update().get(pk=sport.pk)
        if Season.objects.filter(sport=sport, year=year).exists():
            raise SeasonAlreadyExistsError(
                f"A season for {year} already exists. Choose a different year."
            )

        previous: Optional[Season] = Season.objects.filter(sport=sport, is_active=True).first()
        Season.objects.filter(sport=sport, is_active=True).update(is_active=False)

        season = Season.objects.create(
            year=year,
            sport=sport,
            name=season_name,
            is_active=True,
        )

        rules_copied = 0
        if previous:
            rules_copied = _clone_league_rules(previous, season)
        leagues_paused = League.objects.filter(sport=sport).update(
            is_active=False,
            season_opt_in_required=True,
        )

    initialize_queued, initialize_mode, initialize_error = (
        _start_initialize(year) if sport.pk == DEFAULT_SPORT else (False, None, None)
    )

    return {
        "season": season,
        "previous_season": previous,
        "rules_copied": rules_copied,
        "leagues_paused": leagues_paused,
        "initialize_queued": initialize_queued,
        "initialize_mode": initialize_mode,
        "initialize_error": initialize_error,
    }
