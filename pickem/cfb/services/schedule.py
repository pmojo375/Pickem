from datetime import datetime
from typing import Tuple, Optional
from django.utils import timezone
from ..models import Season, Week


def get_current_week(season: Optional[Season] = None, now: Optional[datetime] = None) -> Optional[Week]:
    """
    Get the current Week object based on the current datetime.
    
    Args:
        season: Season to filter by. If None, uses the active season.
        now: Current datetime. If None, uses timezone.now().
    
    Returns:
        Week object if found, None otherwise.
    """
    now = now or timezone.now()
    
    if season is None:
        season = Season.objects.filter(is_active=True).first()
    
    if not season:
        return None
    
    # Convert now to date for comparison
    current_date = now.date()
    
    # First, try to find a week where the current date matches the start_date
    # This handles the case where end_date of previous week matches start_date of current week
    week = Week.objects.filter(
        season=season,
        start_date=current_date
    ).first()
    
    # If no week found with matching start_date, find where current_date falls between start_date and end_date
    if not week:
        week = Week.objects.filter(
            season=season,
            start_date__lte=current_date,
            end_date__gte=current_date
        ).exclude(start_date=current_date).first()
    
    return week


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
