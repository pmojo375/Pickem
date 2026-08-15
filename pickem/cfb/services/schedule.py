from datetime import datetime
from typing import Tuple, Optional
from django.utils import timezone
from ..models import Season, Week


def get_current_week(season: Optional[Season] = None, now: Optional[datetime] = None) -> Optional[Week]:
    """
    Get the Week that actually contains `now` on the calendar.

    Background jobs (spreads, rankings, stats) must use this so they do not
    treat a future week as current before it starts.
    """
    now = now or timezone.now()

    if season is None:
        season = Season.objects.filter(is_active=True).first()

    if not season:
        return None

    current_date = now.date()

    # Prefer a week whose start_date is today (handles adjacent-week overlap)
    week = Week.objects.filter(
        season=season,
        start_date=current_date
    ).first()

    if not week:
        week = Week.objects.filter(
            season=season,
            start_date__lte=current_date,
            end_date__gte=current_date
        ).exclude(start_date=current_date).first()

    return week


def get_display_week(season: Optional[Season] = None, now: Optional[datetime] = None) -> Optional[Week]:
    """
    Week to show in Settings/Picks/Live.

    Same as get_current_week during the season; before week 1, returns the next
    upcoming week so the UI is not empty.
    """
    week = get_current_week(season=season, now=now)
    if week:
        return week

    now = now or timezone.now()
    if season is None:
        season = Season.objects.filter(is_active=True).first()
    if not season:
        return None

    return (
        Week.objects.filter(season=season, start_date__gt=now.date())
        .order_by("start_date")
        .first()
    )


def get_week_datetime_range(week: Week) -> Tuple[datetime, datetime]:
    """
    Convert a Week model's start_date and end_date to timezone-aware datetimes.
    
    Args:
        week: Week object with start_date and end_date
    
    Returns:
        Tuple of (start_datetime, end_datetime) as timezone-aware datetimes
    """
    # Convert dates to datetimes
    # Start at midnight on the start date
    start = datetime.combine(week.start_date, datetime.min.time())
    # End at 23:59:59 on the end date
    end = datetime.combine(week.end_date, datetime.max.time())
    
    # Make timezone-aware
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    if timezone.is_naive(end):
        end = timezone.make_aware(end)
    
    return start, end
