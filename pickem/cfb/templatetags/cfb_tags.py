from django import template
from django.utils import timezone
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

