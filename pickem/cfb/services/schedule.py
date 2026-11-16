from datetime import datetime
from typing import Tuple, Optional
from django.utils import timezone
from django.db.models import Case, When, Value, IntegerField, Exists, OuterRef
from ..models import DEFAULT_SPORT, League, LeagueGame, Season, SchedulePeriod, Game
from .sports import get_active_season


def get_current_period(season: Optional[Season] = None, now: Optional[datetime] = None) -> Optional[SchedulePeriod]:
    """
    Get the SchedulePeriod that actually contains `now` on the calendar.

    Background jobs (spreads, rankings, stats) must use this so they do not
    treat a future schedule_period as current before it starts.
    """
    now = now or timezone.now()

    if season is None:
        season = get_active_season()

    if not season:
        return None

    current_date = timezone.localdate(now)

    candidates = SchedulePeriod.objects.filter(season=season, start_date__lte=current_date, end_date__gte=current_date)
    # Named rounds/slates take precedence over automatically created daily periods.
    candidates = candidates.annotate(
        has_games=Exists(Game.objects.filter(schedule_period_id=OuterRef("pk"))),
        explicit_priority=Case(When(kind="day", then=Value(0)), default=Value(1), output_field=IntegerField()),
    )
    schedule_period = candidates.order_by("-has_games", "-explicit_priority", "-start_date", "number").first()

    return schedule_period


def get_display_period(season: Optional[Season] = None, now: Optional[datetime] = None) -> Optional[SchedulePeriod]:
    """
    SchedulePeriod to show in Settings/Picks.

    Same as get_current_period during the season; before schedule_period 1, returns the next
    upcoming schedule_period so the UI is not empty.
    """
    schedule_period = get_current_period(season=season, now=now)
    if schedule_period:
        return schedule_period

    now = now or timezone.now()
    if season is None:
        season = get_active_season()
    if not season:
        return None

    return (
        SchedulePeriod.objects.filter(season=season, start_date__gt=timezone.localdate(now))
        .order_by("start_date")
        .first()
    )


def get_live_period(league: League, now: Optional[datetime] = None) -> Optional[SchedulePeriod]:
    """Keep the prior schedule_period's slate visible until this league's first start_time.

    Use the same calendar windows as the live page, rather than provider schedule_period
    numbers. Once a current-schedule_period game has kicked off, stay on that schedule_period even
    between games or after all games are final. Picks/Settings remain unchanged.
    """
    now = now or timezone.now()
    season = get_active_season(league.sport_id if league else DEFAULT_SPORT)
    schedule_period = get_display_period(season=season, now=now) if season else None
    if schedule_period is None or league is None:
        return schedule_period

    games = LeagueGame.objects.filter(
        league=league, is_active=True, game__season=schedule_period.season
    )
    start, end = get_period_datetime_range(schedule_period)
    if games.filter(
        **period_game_filters(schedule_period), game__start_time__lte=now
    ).exists():
        return schedule_period

    previous_period = (
        SchedulePeriod.objects.filter(season=schedule_period.season, start_date__lt=schedule_period.start_date)
        .order_by('-start_date', '-pk')
        .first()
    )
    if previous_period:
        start, end = get_period_datetime_range(previous_period)
        if games.filter(
            **period_game_filters(previous_period), game__start_time__lte=now
        ).exists():
            return previous_period
    return schedule_period


def get_period_datetime_range(schedule_period: SchedulePeriod) -> Tuple[datetime, datetime]:
    """
    Convert a SchedulePeriod model's start_date and end_date to timezone-aware datetimes.
    
    Args:
        schedule_period: SchedulePeriod object with start_date and end_date
    
    Returns:
        Tuple of (start_datetime, end_datetime) as timezone-aware datetimes
    """
    # Convert dates to datetimes
    # Start at midnight on the start date
    start = datetime.combine(schedule_period.start_date, datetime.min.time())
    # End at 23:59:59 on the end date
    end = datetime.combine(schedule_period.end_date, datetime.max.time())
    
    # Make timezone-aware
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)
    
    return start, end


def get_display_period_for_league(league, now=None):
    season = get_active_season(league.sport_id if league else DEFAULT_SPORT)
    return get_display_period(season=season, now=now) if season else None


def period_game_filters(schedule_period, prefix="game__"):
    """Football retains provider-calendar grouping; other sports use explicit slates."""
    if schedule_period.season.sport_id == DEFAULT_SPORT:
        return {prefix + "start_time__range": get_period_datetime_range(schedule_period)}
    return {prefix + "schedule_period": schedule_period}
