from decimal import Decimal
from typing import Dict, List, Optional
import requests
from django.conf import settings
from django.utils import timezone
from ..models import Game, GameSpread


def update_odds_for_week_games() -> int:
    """
    Fetch spreads from The Odds API for all games in the current week.
    Only stores spreads if they've changed or don't exist yet.
    Returns the number of games updated.
    """
    api_key = settings.ODDS_API_KEY
    if not api_key:
        return 0
    
    # Get all games for the current week
    from .schedule import get_week_window
    start, end = get_week_window()
    games = Game.objects.filter(kickoff__range=(start, end)).select_related('home_team', 'away_team')
    
    if not games.exists():
        return 0
    
    # Fetch odds from The Odds API (single API call for all games)
    try:
        url = 'https://api.the-odds-api.com/v4/sports/americanfootball_ncaaf/odds/'
        params = {
            'apiKey': api_key,
            'regions': 'us',
            'markets': 'spreads',
            'oddsFormat': 'american',
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        odds_data = response.json()
        
        # Build a lookup map of API games by team names
        odds_by_teams = _build_odds_lookup(odds_data)
        
        updated_count = 0
        for game in games:
            # Try to match the game from our DB with the odds data
            spreads = _find_matching_spreads(game, odds_by_teams)
            if spreads:
                # Check if we need to store this spread (if it changed or doesn't exist)
                if _should_store_spread(game, spreads):
                    GameSpread.objects.create(
                        game=game,
                        home_spread=spreads['home_spread'],
                        away_spread=spreads['away_spread'],
                        source=spreads.get('source', '')
                    )
                    # Also update the current spread fields on the Game model
                    game.current_home_spread = spreads['home_spread']
                    game.current_away_spread = spreads['away_spread']
                    game.save(update_fields=['current_home_spread', 'current_away_spread'])
                    
                    # Set opening spread if not already set
                    if game.opening_home_spread is None:
                        game.opening_home_spread = spreads['home_spread']
                        game.opening_away_spread = spreads['away_spread']
                        game.save(update_fields=['opening_home_spread', 'opening_away_spread'])
                    
                    updated_count += 1
        
        return updated_count
        
    except Exception as e:
        print(f"Error fetching odds: {e}")
        return 0


def _build_odds_lookup(odds_data: List[Dict]) -> Dict:
    """
    Build a lookup dictionary from the odds API response.
    Maps normalized team names to spread data.
    """
    odds_lookup = {}
    
    for event in odds_data:
        home_team = event.get('home_team', '')
        away_team = event.get('away_team', '')
        
        # Get the first bookmaker's spreads (usually consensus or featured book)
        bookmakers = event.get('bookmakers', [])
        if not bookmakers:
            continue
            
        bookmaker = bookmakers[0]  # Use first available bookmaker
        markets = bookmaker.get('markets', [])
        
        spread_market = next((m for m in markets if m.get('key') == 'spreads'), None)
        if not spread_market:
            continue
        
        outcomes = spread_market.get('outcomes', [])
        if len(outcomes) < 2:
            continue
        
        # Extract spreads for home and away teams
        home_outcome = next((o for o in outcomes if o.get('name') == home_team), None)
        away_outcome = next((o for o in outcomes if o.get('name') == away_team), None)
        
        if home_outcome and away_outcome:
            home_spread = home_outcome.get('point')
            away_spread = away_outcome.get('point')
            
            if home_spread is not None and away_spread is not None:
                # Store by both team combinations for flexible matching
                key = _normalize_team_pair(home_team, away_team)
                odds_lookup[key] = {
                    'home_team_api': home_team,
                    'away_team_api': away_team,
                    'home_spread': Decimal(str(home_spread)),
                    'away_spread': Decimal(str(away_spread)),
                    'source': bookmaker.get('title', ''),
                }
    
    return odds_lookup


def _normalize_team_pair(team1: str, team2: str) -> str:
    """Create a normalized key for team pair matching"""
    return f"{_normalize_team_name(team1)}|{_normalize_team_name(team2)}"


def _normalize_team_name(name: str) -> str:
    """Normalize team name for matching"""
    # Remove common suffixes and normalize
    normalized = name.lower().strip()
    # Remove state suffixes if present
    normalized = normalized.replace(' state', '').replace(' st.', '').replace(' st', '')
    return normalized


def _find_matching_spreads(game: Game, odds_lookup: Dict) -> Optional[Dict]:
    """
    Find matching spreads for a game from the odds lookup.
    Tries various name matching strategies.
    """
    home_name = game.home_team.name
    away_name = game.away_team.name
    
    # Try exact match first
    key = _normalize_team_pair(home_name, away_name)
    if key in odds_lookup:
        return odds_lookup[key]
    
    # Try matching against all odds entries with fuzzy matching
    for odds_key, odds_data in odds_lookup.items():
        home_api = odds_data['home_team_api']
        away_api = odds_data['away_team_api']
        
        # Check if team names contain each other or vice versa
        if (_team_names_match(home_name, home_api) and 
            _team_names_match(away_name, away_api)):
            return odds_data
    
    return None


def _team_names_match(db_name: str, api_name: str) -> bool:
    """Check if two team names match with fuzzy logic"""
    db_normalized = _normalize_team_name(db_name)
    api_normalized = _normalize_team_name(api_name)
    
    # Exact match
    if db_normalized == api_normalized:
        return True
    
    # One contains the other
    if db_normalized in api_normalized or api_normalized in db_normalized:
        return True
    
    # Check if main school name matches (e.g., "Michigan" in "Michigan State")
    db_words = set(db_normalized.split())
    api_words = set(api_normalized.split())
    
    # At least 2 words in common for longer names, or 1 for short names
    common_words = db_words & api_words
    if len(db_words) <= 2 or len(api_words) <= 2:
        return len(common_words) >= 1
    return len(common_words) >= 2


def _should_store_spread(game: Game, new_spreads: Dict) -> bool:
    """
    Check if we should store the new spread data.
    Returns True if the spread has changed or doesn't exist yet.
    """
    # Get the most recent spread for this game
    latest_spread = game.spreads.first()  # Already ordered by -timestamp
    
    if not latest_spread:
        return True
    
    # Check if spread has changed (accounting for decimal precision)
    home_changed = abs(latest_spread.home_spread - new_spreads['home_spread']) >= Decimal('0.5')
    away_changed = abs(latest_spread.away_spread - new_spreads['away_spread']) >= Decimal('0.5')
    
    return home_changed or away_changed


