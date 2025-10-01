from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import requests
from django.utils import timezone
from django.conf import settings
from ..models import Season, Team, Game

# Optional CFBD client (installed via `pip install cfbd`)
try:
    import cfbd  # type: ignore
except Exception:  # pragma: no cover
    cfbd = None

# Last CFBD error for debugging in UI
LAST_CFBD_ERROR: str | None = None


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"


def get_week_window(now: datetime | None = None) -> Tuple[datetime, datetime]:
    now = now or timezone.now()
    # Week starts Monday 00:00 and ends Sunday 23:59:59 in local timezone
    monday = now - timedelta(days=(now.weekday()))
    start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return start, end


def fetch_weekly_games(start: datetime, end: datetime) -> List[Dict]:
    events: List[Dict] = []
    day = start
    while day.date() <= end.date():
        params = {"dates": day.strftime("%Y%m%d")}
        resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        day_events = data.get("events", [])
        if day_events:
            events.extend(day_events)
        day = day + timedelta(days=1)
    return events


def _get_or_create_team(season: Season, team_obj: Dict) -> Team:
    # ESPN shape (fallback)
    espn_team_id = str(team_obj.get("id") or team_obj.get("uid", "").split(":")[-1]) if team_obj else None
    school = team_obj.get("location") or team_obj.get("name") or team_obj.get("displayName") or team_obj.get("shortDisplayName")
    nickname = team_obj.get("nickname") or ""
    abbreviation = team_obj.get("abbreviation") or ""
    logo_url = ""
    logos = team_obj.get("logos") or []
    if logos:
        logo_url = logos[0].get("href") or ""

    # Prefer matching by ESPN ID when present within same season
    team = None
    if espn_team_id:
        team = Team.objects.filter(season=season, espn_id=espn_team_id).first()
    if not team:
        team = Team.objects.filter(season=season, name=school).first()

    if team:
        # Update metadata if missing
        updates = {}
        if not team.nickname and nickname:
            updates["nickname"] = nickname
        if not team.logo_url and logo_url:
            updates["logo_url"] = logo_url
        if not team.abbreviation and abbreviation:
            updates["abbreviation"] = abbreviation
        if espn_team_id and not team.espn_id:
            updates["espn_id"] = espn_team_id
        if updates:
            for k, v in updates.items():
                setattr(team, k, v)
            team.save(update_fields=list(updates.keys()))
        return team

    return Team.objects.create(
        season=season,
        name=school,
        nickname=nickname,
        abbreviation=abbreviation,
        logo_url=logo_url,
        espn_id=espn_team_id or None,
    )


def fetch_and_store_week(now: datetime | None = None) -> int:
    start, end = get_week_window(now)
    year = start.year
    season, _ = Season.objects.get_or_create(year=year, defaults={"is_active": True})
    count = 0

    if settings.CFBD_API_KEY:
        return _fetch_and_store_week_cfbd(season, start, end)

    for ev in fetch_weekly_games(start, end):
        competitions = ev.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]
        competitors = comp.get("competitors") or []
        if len(competitors) != 2:
            continue

        # Determine home/away
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        home_team = _get_or_create_team(season, home.get("team") or {})
        away_team = _get_or_create_team(season, away.get("team") or {})

        # kickoff
        date_str = ev.get("date") or comp.get("date")
        if not date_str:
            continue
        try:
            kickoff = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if not (start <= kickoff <= end):
            # Only include this week's window
            continue

        external_id = str(ev.get("id") or comp.get("id") or "")

        game, created = Game.objects.update_or_create(
            season=season,
            external_id=external_id or None,
            defaults={
                "home_team": home_team,
                "away_team": away_team,
                "kickoff": kickoff,
            },
        )
        # If external_id missing, fall back to tuple uniqueness
        if not external_id:
            game, created = Game.objects.get_or_create(
                season=season,
                home_team=home_team,
                away_team=away_team,
                kickoff=kickoff,
            )
        count += 1

    return count


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

                    external_id = str(g.id) if getattr(g, 'id', None) is not None else ""
                    Game.objects.update_or_create(
                        season=season,
                        external_id=external_id or None,
                        defaults={
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


