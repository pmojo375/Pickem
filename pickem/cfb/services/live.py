from ..models import Game, Pick, LeagueGame, Season
import requests
from django.utils import timezone
from datetime import timedelta
from typing import Optional, Dict, Any


# Prefer site.web — site.api is intermittently blocked / rate-limited
ESPN_SCOREBOARD_URLS = (
    "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
)


def _fetch_scoreboard(params: dict) -> Optional[Dict[str, Any]]:
    """Fetch ESPN scoreboard JSON, trying web then site.api."""
    last_error = None
    for url in ESPN_SCOREBOARD_URLS:
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

    try:
        home_score = int(home_competitor.get("score", 0))
        away_score = int(away_competitor.get("score", 0))
    except (ValueError, TypeError):
        return False

    is_final = status_state == "post"
    period = status.get("period")
    clock = status.get("displayClock", "")
    possession = _possession_side(competition, home_competitor, away_competitor, is_final)

    game.home_score = home_score
    game.away_score = away_score
    game.is_final = is_final
    game.quarter = period
    game.clock = clock
    game.possession = possession
    game.save(
        update_fields=[
            "home_score",
            "away_score",
            "is_final",
            "quarter",
            "clock",
            "possession",
        ]
    )

    if is_final:
        grade_picks_for_game(game)

    return True


def fetch_single_game_score(game: Game) -> bool:
    """
    Fetch score for a single game from ESPN API.
    Updates Game record with current score, quarter, clock, possession, and final status.
    
    Returns True if the game was updated, False otherwise.
    """
    if not game.external_id:
        return False
    
    try:
        import pytz
        eastern = pytz.timezone("America/New_York")
        game_date = game.kickoff.astimezone(eastern).date()
        params = {"dates": game_date.strftime("%Y%m%d"), "limit": 300}

        data = _fetch_scoreboard(params)
        if not data:
            return False

        for event in data.get("events", []):
            if str(event.get("id", "")) == game.external_id:
                return _apply_event_to_game(game, event)

        return False
        
    except requests.RequestException as e:
        print(f"Error fetching ESPN scores for game {game.id}: {e}")
        return False


def grade_picks_for_game(game: Game) -> int:
    """
    Grade all picks for a completed game based on spread.
    Returns the number of picks graded.
    """
    if not game.is_final:
        return 0
    
    if game.home_score is None or game.away_score is None:
        return 0
    
    graded_count = 0
    
    # Get all league games for this game
    league_games = LeagueGame.objects.filter(game=game, is_active=True)
    
    for league_game in league_games:
        # Skip if no locked spread
        if league_game.locked_home_spread is None:
            continue
        
        # Calculate the actual spread
        # Home spread is the line for the home team
        # Negative spread means home team is favored
        # e.g., home_spread = -7 means home needs to win by more than 7 to cover
        # e.g., home_spread = +3 means away is favored, home can lose by up to 3 and still cover
        actual_margin = game.home_score - game.away_score
        spread = float(league_game.locked_home_spread)
        
        # Determine which team covered the spread
        # actual_margin > -spread means home covered
        # Example: Home -7, wins by 10: margin=10 > -(-7)=7 → TRUE, home covers
        # Example: Home -7, wins by 5: margin=5 > -(-7)=7 → FALSE, away covers
        # Example: Home +3, loses by 2: margin=-2 > -(3)=-3 → TRUE, home covers
        home_covered = actual_margin > -spread
        
        # Get all picks for this game in this league
        picks = Pick.objects.filter(game=game, league=league_game.league, is_correct__isnull=True)
        
        for pick in picks:
            if pick.picked_team_id == game.home_team_id:
                pick.is_correct = home_covered
            else:
                pick.is_correct = not home_covered
            
            pick.save(update_fields=['is_correct'])
            graded_count += 1
    
    return graded_count


def fetch_and_store_live_scores() -> int:
    """
    Fetch live scores from ESPN API for games that have started or finished.
    Updates Game records with current scores, quarter, clock, possession, and final status.
    """
    import pytz

    updated = 0
    
    # Get all games from the current week that aren't finalized yet or need checking
    now = timezone.now()
    start_of_week = now - timedelta(days=7)
    
    # Get all games in current window for the active season
    games_qs = Game.objects.filter(
        kickoff__gte=start_of_week,
        kickoff__lte=now + timedelta(days=1)
    ).select_related('home_team', 'away_team')
    active_season = Season.objects.filter(is_active=True).first()
    if active_season:
        games_qs = games_qs.filter(season=active_season)

    games = list(games_qs)
    if not games:
        return 0

    # ESPN scoreboard dates are US/Eastern calendar days, not UTC.
    eastern = pytz.timezone("America/New_York")
    dates_needed = {now.astimezone(eastern).date()}
    for game in games:
        if game.kickoff:
            dates_needed.add(game.kickoff.astimezone(eastern).date())

    events_by_espn_id = {}
    for check_date in sorted(dates_needed):
        try:
            params = {"dates": check_date.strftime("%Y%m%d"), "limit": 300}
            data = _fetch_scoreboard(params)
            if not data:
                continue
            for event in data.get("events", []):
                event_id = str(event.get("id", ""))
                if event_id:
                    events_by_espn_id[event_id] = event
        except requests.RequestException as e:
            # Don't abort the whole poll if one day fails — other dates can still update.
            print(f"Error fetching ESPN scores for {check_date}: {e}")
            continue

    if not events_by_espn_id:
        return 0

    for game in games:
        if not game.external_id or game.external_id not in events_by_espn_id:
            continue

        if _apply_event_to_game(game, events_by_espn_id[game.external_id]):
            updated += 1

    return updated
