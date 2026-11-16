from ..models import Game
import logging
import requests
from django.utils import timezone
from datetime import timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# Prefer site.web — site.api is intermittently blocked / rate-limited
ESPN_SCOREBOARD_URLS = (
    "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
)


def _fetch_scoreboard(params: dict, sport=None) -> Optional[Dict[str, Any]]:
    """Fetch ESPN scoreboard JSON, trying web then site.api."""
    last_error = None
    urls = sport.scoreboard_urls if sport is not None else ESPN_SCOREBOARD_URLS
    for url in urls:
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    return None


def _possession_side(competition, home_competitor, away_competitor, is_final: bool) -> str:
    """
    Map ESPN situation.possession (ESPN team id) to 'home' / 'away' / ''.
    """
    if is_final:
        return ""
    situation = competition.get("situation") or {}
    possession_id = situation.get("possession")
    if possession_id is None or possession_id == "":
        return ""
    possession_id = str(possession_id)
    if str(home_competitor.get("id", "")) == possession_id:
        return "home"
    if str(away_competitor.get("id", "")) == possession_id:
        return "away"
    return ""


def _situation_down_and_distance(competition, is_final: bool) -> tuple[str, str]:
    """
    Extract down & distance / ball spot from ESPN competition.situation.

    Returns (down_distance_text, ball_on), e.g. ("3rd & 5", "MSST 30").
    Cleared when the game is final or ESPN has no active down (start_time/XP/etc).
    """
    if is_final:
        return "", ""
    situation = competition.get("situation") or {}
    if not situation:
        return "", ""

    # ESPN uses down/distance of -1 between plays (extra points, start_times, etc.)
    down = situation.get("down")
    try:
        down_num = int(down) if down is not None and down != "" else 0
    except (TypeError, ValueError):
        down_num = 0

    short = (situation.get("shortDownDistanceText") or "").strip()
    full = (situation.get("downDistanceText") or "").strip()
    ball_on = (situation.get("possessionText") or "").strip()

    if down_num > 0 and short:
        return short[:32], ball_on[:32]
    if down_num > 0 and full:
        # Fallback if short text is missing
        return full[:32], ball_on[:32]
    return "", ""


def _apply_event_to_game(game: Game, event: dict) -> bool:
    """Update a Game from an ESPN scoreboard event. Returns True if applied."""
    status = event.get("status", {})
    status_type = status.get("type", {})
    status_state = status_type.get("state", "")

    if status_state not in ["in", "post"]:
        return False

    competitions = event.get("competitions", [])
    if not competitions:
        return False

    competition = competitions[0]
    competitors = competition.get("competitors", [])

    home_competitor = None
    away_competitor = None
    for competitor in competitors:
        if competitor.get("homeAway") == "home":
            home_competitor = competitor
        elif competitor.get("homeAway") == "away":
            away_competitor = competitor

    if not home_competitor or not away_competitor:
        return False

    def _parse_score(raw) -> int:
        # ESPN often sends "" for a true 0 before the first points.
        if raw is None or raw == "":
            return 0
        return int(raw)

    try:
        home_score = _parse_score(home_competitor.get("score", 0))
        away_score = _parse_score(away_competitor.get("score", 0))
    except (ValueError, TypeError):
        # Still apply period/clock if score text is weird — default to 0.
        home_score = 0
        away_score = 0

    is_final = status_state == "post"
    period = status.get("period")
    clock = status.get("displayClock", "")
    possession = _possession_side(competition, home_competitor, away_competitor, is_final)
    down_distance_text, ball_on = _situation_down_and_distance(competition, is_final)
    sport = game.season.sport
    if not sport.has_possession:
        possession = ""
    if not sport.has_downs:
        down_distance_text, ball_on = "", ""

    game.home_score = home_score
    game.away_score = away_score
    game.is_final = is_final
    game.period = period
    game.clock = clock
    game.possession = possession
    game.down_distance_text = down_distance_text
    game.ball_on = ball_on
    game.save(
        update_fields=[
            "home_score",
            "away_score",
            "is_final",
            "period",
            "clock",
            "possession",
            "down_distance_text",
            "ball_on",
        ]
    )
    # Grading + MemberPeriod updates happen in the Game post_save signal
    # (cfb.signals.game_finalized) when the game newly becomes final.

    return True


def fetch_single_game_score(game: Game) -> bool:
    """
    Fetch score for a single game from ESPN API.
    Updates Game record with current score, period, clock, possession,
    down/distance, and final status.
    
    Returns True if the game was updated, False otherwise.
    """
    espn_id = str(game.provider_ids.get("espn") or game.external_id or "")
    if not espn_id or not game.season.sport.is_active or not game.season.sport.scoreboard_urls:
        return False
    
    try:
        import pytz
        eastern = pytz.timezone("America/New_York")
        game_date = game.start_time.astimezone(eastern).date()
        params = {"dates": game_date.strftime("%Y%m%d"), "limit": 300}

        data = _fetch_scoreboard(params, sport=game.season.sport)
        if not data:
            return False

        for event in data.get("events", []):
            if str(event.get("id", "")) == espn_id:
                return _apply_event_to_game(game, event)

        return False
        
    except requests.RequestException as e:
        logger.warning("Error fetching ESPN scores for game %s: %s", game.id, e)
        return False


def grade_picks_for_game(game: Game) -> int:
    """
    Grade picks and refresh MemberPeriod/MemberSeason for a completed game.

    Delegates to scoring.update_member_period_for_game so ATS / force_hooks /
    push handling stays in one place.
    """
    from .scoring import update_member_period_for_game

    return update_member_period_for_game(game)


def fetch_and_store_live_scores() -> int:
    """
    Fetch live scores from ESPN API for games that have started or finished.
    Updates Game records with current scores, period, clock, possession,
    down/distance, and final status.
    """
    import pytz

    updated = 0
    
    # Get all games from the current schedule_period that aren't finalized yet or need checking
    now = timezone.now()
    start_of_period = now - timedelta(days=7)
    
    # Get all games in current window for the active season
    games_qs = Game.objects.filter(
        start_time__gte=start_of_period,
        start_time__lte=now + timedelta(days=1)
    ).filter(season__is_active=True).select_related('home_team', 'away_team', 'season__sport')

    games = list(games_qs)
    if not games:
        return 0

    # ESPN scoreboard dates are US/Eastern calendar days, not UTC.
    eastern = pytz.timezone("America/New_York")
    # IDs and scoreboards belong to a sport namespace, never a global feed.
    events_by_sport = {}
    sports = {game.season.sport_id: game.season.sport for game in games}
    for sport_id, sport in sports.items():
        if not sport.is_active or not sport.scoreboard_urls:
            continue
        events = events_by_sport[sport_id] = {}
        sport_dates = {game.start_time.astimezone(eastern).date() for game in games if game.season.sport_id == sport_id}
        for check_date in sorted(sport_dates):
            try:
                params = {"dates": check_date.strftime("%Y%m%d"), "limit": 300}
                data = _fetch_scoreboard(params, sport=sport)
                for event in (data or {}).get("events", []):
                    event_id = str(event.get("id", ""))
                    if event_id:
                        events[event_id] = event
            except requests.RequestException as exc:
                logger.warning("Error fetching ESPN scores for %s %s: %s", sport_id, check_date, exc)

    for game in games:
        espn_id = str(game.provider_ids.get("espn") or game.external_id or "")
        event = events_by_sport.get(game.season.sport_id, {}).get(espn_id)
        if event and _apply_event_to_game(game, event):
            updated += 1

    return updated
