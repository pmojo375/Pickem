from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import requests
from django.utils import timezone
from django.conf import settings
from ..models import Season, Team, Game, Week

# Optional CFBD client (installed via `pip install cfbd`)
try:
    import cfbd  # type: ignore
except Exception:  # pragma: no cover
    cfbd = None

# Last CFBD error for debugging in UI
LAST_CFBD_ERROR: str | None = None


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
    
    # Find the week where current_date falls between start_date and end_date
    week = Week.objects.filter(
        season=season,
        start_date__lte=current_date,
        end_date__gte=current_date
    ).first()
    
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


def get_week_window(now: datetime | None = None) -> Tuple[datetime, datetime]:
    now = now or timezone.now()
    # Week starts Monday 00:00 and ends Sunday 12:00:00 (noon) in local timezone
    monday = now - timedelta(days=(now.weekday()))
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=6, hours=12)  # Sunday at noon
    return start, end




def fetch_and_store_week(now: datetime | None = None) -> int:
    """
    Fetch and store games for the current week using CFBD API.
    
    Returns:
        Number of games imported
    """
    start, end = get_week_window(now)
    year = start.year
    season, _ = Season.objects.get_or_create(year=year, defaults={"is_active": True})

    if not settings.CFBD_API_KEY:
        # CFBD API key is required
        return 0

    return _fetch_and_store_week_cfbd(season, start, end)


def _get_overlapping_weeks(api, year: int, start: datetime, end: datetime, api_key: str) -> List[int]:
    """
    Determine which CFBD week numbers overlap with the given date window.
    Tries SDK CalendarApi first, falls back to REST API, then defaults to all weeks.
    """
    week_numbers: List[int] = []
    
    # Try SDK CalendarApi if available
    calendar_api_cls = getattr(cfbd, 'CalendarApi', None)
    if calendar_api_cls is not None:
        try:
            cal = calendar_api_cls(api).get_calendar(year=year)
            for wk in cal:
                week_start = getattr(wk, 'first_game_start', None)
                week_end = getattr(wk, 'last_game_start', None)
                week_num = getattr(wk, 'week', None)
                
                if week_start and week_end and week_num is not None:
                    start_date = datetime.fromisoformat(str(week_start).replace("Z", "+00:00"))
                    end_date = datetime.fromisoformat(str(week_end).replace("Z", "+00:00")) + timedelta(days=1) - timedelta(seconds=1)
                    
                    # Check if week overlaps with our window
                    if not (end < start_date or start > end_date):
                        week_numbers.append(int(week_num))
            
            if week_numbers:
                return week_numbers
        except Exception:
            pass  # Fall through to REST API
    
    # Fallback to REST calendar API
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        r = requests.get(
            "https://api.collegefootballdata.com/calendar",
            params={"year": year},
            headers=headers,
            timeout=20
        )
        r.raise_for_status()
        
        for wk in r.json():
            week_start = wk.get('firstGameStart')
            week_end = wk.get('lastGameStart')
            week_num = wk.get('week')
            
            if week_start and week_end and week_num is not None:
                start_date = datetime.fromisoformat(str(week_start).replace("Z", "+00:00"))
                end_date = datetime.fromisoformat(str(week_end).replace("Z", "+00:00")) + timedelta(days=1) - timedelta(seconds=1)
                
                # Check if week overlaps with our window
                if not (end < start_date or start > end_date):
                    week_numbers.append(int(week_num))
        
        if week_numbers:
            return week_numbers
    except Exception:
        pass  # Fall through to default
    
    # Default: return all possible weeks
    return list(range(1, 21))


def _normalize_color(color) -> str:
    """Normalize color hex code by ensuring it starts with #"""
    if color is None:
        return ""
    color_str = str(color)
    if not color_str.startswith('#'):
        return f"#{color_str}"
    return color_str


def _sync_cfbd_team(season: Season, team_data) -> Team | None:
    """
    Sync a CFBD team to the database. Creates or updates team with latest data.
    This is the authoritative source for FBS team metadata.
    """
    if not team_data:
        return None
    
    # Extract team data
    cfbd_id = team_data.id
    school_name = team_data.school
    nickname = getattr(team_data, 'mascot', '') or ""
    abbreviation = getattr(team_data, 'abbreviation', '') or ""
    conference = getattr(team_data, 'conference', '') or ""
    twitter = getattr(team_data, 'twitter', '') or ""
    city = getattr(team_data, 'location', '') or ""
    
    # Extract logo URL
    logos = getattr(team_data, 'logos', None)
    logo_url = logos[0] if isinstance(logos, list) and logos else ""
    
    # Extract colors
    primary_color = _normalize_color(getattr(team_data, 'color', None))
    alt_color = _normalize_color(getattr(team_data, 'alt_color', None))
    
    # Try to find existing team by CFBD ID first, then by name
    team = Team.objects.filter(season=season, cfbd_id=cfbd_id).first()
    if not team:
        team = Team.objects.filter(season=season, name=school_name).first()
    
    if team:
        # Update existing team with latest data
        team.name = school_name
        team.cfbd_id = cfbd_id
        team.nickname = nickname
        team.abbreviation = abbreviation
        team.conference = conference
        team.logo_url = logo_url
        team.primary_color = primary_color
        team.alt_color = alt_color
        team.twitter = twitter
        team.city = city
        team.save()
        return team
    
    # Create new team
    return Team.objects.create(
        season=season,
        name=school_name,
        cfbd_id=cfbd_id,
        nickname=nickname,
        abbreviation=abbreviation,
        conference=conference,
        logo_url=logo_url,
        primary_color=primary_color,
        alt_color=alt_color,
        twitter=twitter,
        city=city,
    )


def _fetch_and_store_week_cfbd(season: Season, start: datetime, end: datetime) -> int:
    if cfbd is None:
        # Library not installed; fall back to requests path
        return 0

    # Configure CFBD client
    cfg = cfbd.Configuration()
    raw_key = (settings.CFBD_API_KEY or '').strip()
    if raw_key.lower().startswith('bearer '):
        raw_key = raw_key[7:].strip()
    # Support both client auth styles
    try:
        cfg.access_token = raw_key  # some versions prefer access_token
    except Exception:
        pass
    cfg.api_key['Authorization'] = raw_key
    cfg.api_key_prefix['Authorization'] = 'Bearer'
    count = 0

    global LAST_CFBD_ERROR
    try:
        with cfbd.ApiClient(cfg) as api:
            # Determine which week numbers overlap with our date window
            week_numbers = _get_overlapping_weeks(api, season.year, start, end, raw_key)

            # Load authoritative FBS teams (single API call)
            teams_api = cfbd.TeamsApi(api)
            fbs_teams = teams_api.get_fbs_teams(year=season.year)
            
            # Store ALL FBS teams, not just ones with games this week
            cfbd_by_school = {}
            for team_data in fbs_teams:
                team = _sync_cfbd_team(season, team_data)
                if team:
                    cfbd_by_school[team_data.school] = team

            games_api = cfbd.GamesApi(api)
            # Iterate calculated weeks and filter to our window
            for week in sorted(set(week_numbers)):
                # Some cfbd client versions don't support 'division' arg; fetch all and filter to FBS via team map
                games = games_api.get_games(year=season.year, season_type="regular", week=int(week))
                for g in games:
                    # kickoff
                    if not g.start_date:
                        continue
                    try:
                        kickoff = datetime.fromisoformat(str(g.start_date).replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if not (start <= kickoff <= end):
                        continue

                    home_name = g.home_team
                    away_name = g.away_team
                    if not home_name or not away_name:
                        continue

                    # Filter to FBS vs FBS games only
                    if home_name not in cfbd_by_school or away_name not in cfbd_by_school:
                        continue
                    
                    home_team = cfbd_by_school[home_name]
                    away_team = cfbd_by_school[away_name]

                    # Find the Week object for this game based on kickoff date
                    game_week = Week.objects.filter(
                        season=season,
                        start_date__lte=kickoff.date(),
                        end_date__gte=kickoff.date()
                    ).first()

                    external_id = str(g.id) if getattr(g, 'id', None) is not None else ""
                    Game.objects.update_or_create(
                        season=season,
                        external_id=external_id or None,
                        defaults={
                            "week": game_week,
                            "home_team": home_team,
                            "away_team": away_team,
                            "kickoff": kickoff,
                        },
                    )
                    count += 1
        LAST_CFBD_ERROR = None
        return count
    except Exception as e:
        # If CFBD fails, record error and return 0 so caller can fallback
        LAST_CFBD_ERROR = f"{type(e).__name__}: {e}"
        return 0


