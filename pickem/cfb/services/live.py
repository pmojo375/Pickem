from ..models import Game, Pick, LeagueGame
import requests
from django.utils import timezone
from datetime import timedelta


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"


def fetch_single_game_score(game: Game) -> bool:
    """
    Fetch score for a single game from ESPN API.
    Updates Game record with current score, quarter, clock, and final status.
    
    Returns True if the game was updated, False otherwise.
    """
    if not game.external_id:
        return False
    
    try:
        # Get the game date for the API request
        game_date = game.kickoff.date()
        params = {"dates": game_date.strftime("%Y%m%d")}
        
        resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        
        events = data.get("events", [])
        
        # Find our game in the events
        for event in events:
            event_id = str(event.get("id", ""))
            if event_id == game.external_id:
                # Check game status
                status = event.get("status", {})
                status_type = status.get("type", {})
                status_state = status_type.get("state", "")
                
                # Only update games that are in progress or completed
                if status_state not in ["in", "post"]:
                    return False
                
                # Get competitions (usually just one)
                competitions = event.get("competitions", [])
                if not competitions:
                    return False
                
                competition = competitions[0]
                competitors = competition.get("competitors", [])
                
                # Find home and away teams and their scores
                home_competitor = None
                away_competitor = None
                
                for competitor in competitors:
                    if competitor.get("homeAway") == "home":
                        home_competitor = competitor
                    elif competitor.get("homeAway") == "away":
                        away_competitor = competitor
                
                if not home_competitor or not away_competitor:
                    return False
                
                # Extract scores
                try:
                    home_score = int(home_competitor.get("score", 0))
                    away_score = int(away_competitor.get("score", 0))
                except (ValueError, TypeError):
                    return False
                
                # Extract game clock and period
                is_final = status_state == "post"
                period = status.get("period")
                clock = status.get("displayClock", "")
                
                # Update the game
                game.home_score = home_score
                game.away_score = away_score
                game.is_final = is_final
                game.quarter = period
                game.clock = clock
                game.save(update_fields=["home_score", "away_score", "is_final", "quarter", "clock"])
                
                # If game is final, grade the picks
                if is_final:
                    grade_picks_for_game(game)
                
                return True
        
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
    Updates Game records with current scores, quarter, clock, and final status.
    """
    updated = 0
    
    # Get all games from the current week that aren't finalized yet or need checking
    now = timezone.now()
    start_of_week = now - timedelta(days=7)
    
    # Get all games in current window
    games = Game.objects.filter(
        kickoff__gte=start_of_week,
        kickoff__lte=now + timedelta(days=1)
    ).select_related('home_team', 'away_team')
    
    if not games.exists():
        return 0
    
    # Fetch scores from ESPN for current date range
    # ESPN API works best with date parameters
    try:
        # Fetch scoreboard data for a range of days
        events_by_espn_id = {}
        
        # Try fetching for the last 7 days
        for days_ago in range(7):
            check_date = (now - timedelta(days=days_ago)).date()
            params = {"dates": check_date.strftime("%Y%m%d")}
            
            resp = requests.get(ESPN_SCOREBOARD_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            
            events = data.get("events", [])
            for event in events:
                event_id = str(event.get("id", ""))
                if event_id:
                    events_by_espn_id[event_id] = event
        
        # Now update our games with the fetched data
        for game in games:
            if not game.external_id or game.external_id not in events_by_espn_id:
                continue
            
            event = events_by_espn_id[game.external_id]
            
            # Check game status
            status = event.get("status", {})
            status_type = status.get("type", {})
            status_state = status_type.get("state", "")
            
            # Only update games that are in progress or completed
            if status_state not in ["in", "post"]:
                continue
            
            # Get competitions (usually just one)
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            
            competition = competitions[0]
            competitors = competition.get("competitors", [])
            
            # Find home and away teams and their scores
            home_competitor = None
            away_competitor = None
            
            for competitor in competitors:
                if competitor.get("homeAway") == "home":
                    home_competitor = competitor
                elif competitor.get("homeAway") == "away":
                    away_competitor = competitor
            
            if not home_competitor or not away_competitor:
                continue
            
            # Extract scores
            try:
                home_score = int(home_competitor.get("score", 0))
                away_score = int(away_competitor.get("score", 0))
            except (ValueError, TypeError):
                continue
            
            # Extract game clock and period
            is_final = status_state == "post"
            period = status.get("period")
            clock = status.get("displayClock", "")
            
            # Update the game
            game.home_score = home_score
            game.away_score = away_score
            game.is_final = is_final
            game.quarter = period
            game.clock = clock
            game.save(update_fields=["home_score", "away_score", "is_final", "quarter", "clock"])
            
            # If game is final, grade the picks
            if is_final:
                grade_picks_for_game(game)
            
            updated += 1
    
    except requests.RequestException as e:
        print(f"Error fetching ESPN scores: {e}")
        return updated
    
    return updated


