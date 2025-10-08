from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe
import pytz

register = template.Library()


@register.filter
def team_logo_url(team):
    """Return the static URL for a team's logo based on CFBD ID"""
    if team and team.cfbd_id:
        return f'logos/{team.cfbd_id}.png'
    return None


@register.filter
def eastern_time(value):
    """Convert datetime to Eastern timezone and format nicely"""
    if not value:
        return ""
    
    # Get Eastern timezone
    eastern = pytz.timezone('America/New_York')
    
    # Convert to Eastern if not already
    if timezone.is_aware(value):
        value = value.astimezone(eastern)
    else:
        # If naive, assume it's UTC and convert
        value = timezone.make_aware(value, pytz.UTC).astimezone(eastern)
    
    # Format: "Mon, Oct 1 at 3:30 PM ET"
    # Use cross-platform formatting (Windows doesn't support -)
    day = value.day
    hour = value.strftime('%I').lstrip('0') or '12'
    return f"{value.strftime('%a, %b')} {day} at {hour}:{value.strftime('%M %p')} ET"


@register.filter
def has_started(game):
    """
    Check if a game has started.
    A game has started if:
    - It has a quarter value (in progress or finished)
    - OR the current time is past the kickoff time
    """
    if game.quarter is not None:
        return True
    
    if game.kickoff:
        now = timezone.now()
        return now >= game.kickoff
    
    return False


@register.filter
def display_score(game, team_position):
    """
    Display the score for a team, showing:
    - Actual score if available
    - "0" if game has started but no score recorded
    - "-" if game hasn't started yet
    
    Args:
        game: The game object
        team_position: Either "home" or "away"
    """
    score = game.home_score if team_position == "home" else game.away_score
    
    if score is not None:
        return score
    
    # Check if game has started
    if has_started(game):
        return "0"
    else:
        return "—"


@register.simple_tag
def game_spread(game, use_pick_spread=False):
    """
    Display the spread from the home team's perspective.
    Returns just the spread value like: "-7" or "+10" or "PK"
    
    Args:
        game: The game object
        use_pick_spread: If True, use the locked pick spread. If False, use current spread.
    """
    # Determine which spread to use
    if use_pick_spread:
        home_spread_val = game.pick_home_spread
    else:
        home_spread_val = game.current_home_spread
    
    if home_spread_val is None:
        return ""
    
    home_spread = float(home_spread_val)
    
    # Return just the spread value
    if home_spread == 0:
        return "PK"
    else:
        # Format without decimal if whole number, otherwise show one decimal
        if home_spread == int(home_spread):
            return f"{int(home_spread):+d}"
        else:
            return f"{home_spread:+.1f}"


@register.filter
def team_won_game(team, game):
    """
    Check if a team won the game.
    Returns True if team won, False if lost, None if game not final or no scores.
    """
    if not game.is_final or game.home_score is None or game.away_score is None:
        return None
    
    if game.home_score == game.away_score:
        return None  # Tie (rare in CFB but possible)
    
    if team.id == game.home_team_id:
        return game.home_score > game.away_score
    elif team.id == game.away_team_id:
        return game.away_score > game.home_score
    
    return None


@register.simple_tag
def team_covered_spread(team, game, locked_home_spread):
    """
    Check if a team covered the spread.
    Returns True if team covered, False if didn't cover, None if not final or no spread.
    
    Args:
        team: The team to check
        game: The game object
        locked_home_spread: The locked spread for this league's game
    """
    if not game.is_final or game.home_score is None or game.away_score is None:
        return None
    
    if locked_home_spread is None:
        return None
    
    # Calculate margin and coverage (same logic as grade_picks_for_game)
    actual_margin = game.home_score - game.away_score
    spread = float(locked_home_spread)
    home_covered = actual_margin > -spread
    
    if team.id == game.home_team_id:
        return home_covered
    elif team.id == game.away_team_id:
        return not home_covered
    
    return None


@register.filter
def pick_result_badge(pick):
    """
    Return badge HTML for pick result.
    """
    if pick.is_correct is None:
        return ""
    
    if pick.is_correct:
        icon = '<i class="fas fa-check-circle mr-1"></i>'
        return mark_safe(f'<span class="badge badge-success badge-sm">{icon}Correct</span>')
    else:
        icon = '<i class="fas fa-times-circle mr-1"></i>'
        return mark_safe(f'<span class="badge badge-error badge-sm">{icon}Wrong</span>')


@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary by key.
    Usage: {{ mydict|get_item:mykey }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)
