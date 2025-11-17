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
    
    try:
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
        minute = value.strftime('%M')
        am_pm = value.strftime('%p')
        month_abbr = value.strftime('%b')
        day_abbr = value.strftime('%a')
        
        return f"{day_abbr}, {month_abbr} {day} at {hour}:{minute} {am_pm} ET"
    except Exception as e:
        # Log the error but return a safe fallback
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error formatting eastern_time for value {value}: {e}")
        return str(value)  # Return raw value as fallback


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


@register.filter
def apply_hooks(spread, force_hooks=False):
    """
    Apply hooks (half-point spreads) to a spread value if force_hooks is enabled.
    If the spread is a whole number and force_hooks is True, round up to the next half point.
    
    Args:
        spread: The spread value (can be None, int, float, or Decimal)
        force_hooks: Whether to force hooks (half-point spreads)
    
    Returns:
        The spread value with hooks applied if necessary, or the original value
    """
    if spread is None:
        return spread
    
    if not force_hooks:
        return spread
    
    # Convert to float for processing
    try:
        spread_val = float(spread)
    except (ValueError, TypeError):
        return spread
    
    # Check if it's a whole number
    if spread_val == int(spread_val):
        # Round up to next half point (e.g., 3.0 -> 3.5, -3.0 -> -3.5)
        if spread_val > 0:
            return spread_val + 0.5
        elif spread_val < 0:
            return spread_val - 0.5
        else:
            # For 0, we keep it as Pick 'Em
            return spread_val
    
    # Already has a hook, return as is
    return spread_val


@register.filter
def format_spread_display(spread, force_hooks=False):
    """
    Format a spread value for display, applying hooks if needed.

    Args:
        spread: The spread value
        force_hooks: Whether to force hooks

    Returns:
        Formatted string like "-7", "-7.5", etc.
    """
    if spread is None:
        return ""

    # Apply hooks if needed
    spread_val = apply_hooks(spread, force_hooks)

    try:
        spread_float = float(spread_val)
    except (ValueError, TypeError):
        return str(spread)

    if spread_float == 0:
        return "0"

    # Format with one decimal place if not whole number
    if spread_float == int(spread_float):
        return f"{int(spread_float)}"
    else:
        return f"{spread_float:.1f}"


@register.simple_tag
def get_team_stats_organized(team_stats, team_id):
    """
    Organize team stats into categories for display.
    Returns a dict with: mobile_stats, desktop_stats, expandable_stats, all_stats
    """
    if not team_stats or team_id not in team_stats:
        return {
            'mobile_stats': [],
            'desktop_stats': [],
            'expandable_stats': [],
            'all_stats': []
        }
    
    stats = team_stats[team_id]
    
    # Stat name mappings from API to display names
    stat_display_names = {
        'totalYards': 'Total Yards',
        'totalYardsOpponent': 'Total Yards (Opponent)',
        'rushingYards': 'Rush Yards',
        'rushingYardsOpponent': 'Rush Yards (Opponent)',
        'netPassingYards': 'Pass Yards',
        'netPassingYardsOpponent': 'Pass Yards (Opponent)',
        'thirdDownConversions': 'Third Down Conversions',
        'thirdDowns': 'Third Down Attempts',
        'thirdDownConversionsOpponent': 'Third Down Conversions (Opponent)',
        'thirdDownsOpponent': 'Third Down Attempts (Opponent)',
        'turnovers': 'Turnovers',
        'turnoversOpponent': 'Turnovers (Opponent)',
        'possessionTime': 'Time of Possession',
        'firstDowns': 'First Downs',
        'firstDownsOpponent': 'First Downs (Opponent)',
        'penalties': 'Penalties',
        'penaltyYards': 'Penalty Yards',
        'penaltiesOpponent': 'Penalties (Opponent)',
        'penaltyYardsOpponent': 'Penalty Yards (Opponent)',
        'sacks': 'Sacks',
        'tacklesForLoss': 'Tackles for Loss',
        'sacksOpponent': 'Sacks (Opponent)',
        'tacklesForLossOpponent': 'Tackles for Loss (Opponent)',
        'fourthDownConversions': '4th Down Conversions',
        'fourthDowns': '4th Down Attempts',
        'fourthDownConversionsOpponent': '4th Down Conversions (Opponent)',
        'fourthDownsOpponent': '4th Down Attempts (Opponent)',
    }
    
    def get_stat_value(stat_name):
        return stats.get(stat_name, 0)
    
    def format_stat(stat_name, value):
        display_name = stat_display_names.get(stat_name, stat_name)
        return {'name': display_name, 'value': value, 'raw_name': stat_name}
    
    # Calculate derived stats
    total_yards_off = get_stat_value('totalYards')
    total_yards_def = get_stat_value('totalYardsOpponent')
    rush_yards = get_stat_value('rushingYards')
    pass_yards = get_stat_value('netPassingYards')
    
    # Third down %
    third_conv = get_stat_value('thirdDownConversions')
    third_attempts = get_stat_value('thirdDowns')
    third_pct = (third_conv / third_attempts * 100) if third_attempts > 0 else 0
    
    # Turnover margin
    turnovers = get_stat_value('turnovers')
    turnovers_opp = get_stat_value('turnoversOpponent')
    turnover_margin = int(turnovers_opp - turnovers)  # Convert to int for +d format
    
    # Time of possession (in minutes, API returns seconds)
    possession_time_sec = get_stat_value('possessionTime')
    possession_time_min = possession_time_sec / 60 if possession_time_sec else 0
    
    # Mobile stats (6 stats)
    mobile_stats = [
        {'name': 'Total Yards (O / D)', 'value': f"{total_yards_off:,.0f} / {total_yards_def:,.0f}"},
        {'name': 'Rush Yards', 'value': f"{rush_yards:,.0f}"},
        {'name': 'Pass Yards', 'value': f"{pass_yards:,.0f}"},
        {'name': 'Third Down %', 'value': f"{third_pct:.1f}%"},
        {'name': 'Turnover Margin', 'value': f"{turnover_margin:+d}"},
        {'name': 'Time of Possession', 'value': f"{possession_time_min:.1f} min" if possession_time_min else "N/A"},
    ]
    
    # Desktop/expandable stats
    first_downs = get_stat_value('firstDowns')
    first_downs_opp = get_stat_value('firstDownsOpponent')
    penalties = get_stat_value('penalties')
    penalty_yards = get_stat_value('penaltyYards')
    sacks = get_stat_value('sacks')
    tfl = get_stat_value('tacklesForLoss')
    fourth_conv = get_stat_value('fourthDownConversions')
    fourth_attempts = get_stat_value('fourthDowns')
    fourth_pct = (fourth_conv / fourth_attempts * 100) if fourth_attempts > 0 else 0
    
    desktop_stats = [
        {'name': 'First Downs', 'value': f"{first_downs} / {first_downs_opp}"},
        {'name': 'Penalties', 'value': f"{penalties} ({penalty_yards} yds)"},
        {'name': 'Sack/TFL', 'value': f"{sacks} sacks / {tfl} TFL"},
        {'name': '4th Down Stats', 'value': f"{fourth_pct:.1f}% ({fourth_conv}/{fourth_attempts})"},
    ]
    
    # All other stats
    expandable_stats = []
    processed_stats = {
        'totalYards', 'totalYardsOpponent', 'rushingYards', 'netPassingYards',
        'thirdDownConversions', 'thirdDowns', 'turnovers', 'turnoversOpponent',
        'possessionTime', 'firstDowns', 'firstDownsOpponent', 'penalties',
        'penaltyYards', 'sacks', 'tacklesForLoss', 'fourthDownConversions',
        'fourthDowns', 'fourthDownConversionsOpponent', 'fourthDownsOpponent',
        'thirdDownConversionsOpponent', 'thirdDownsOpponent'
    }
    
    for stat_name, value in stats.items():
        if stat_name not in processed_stats:
            display_name = stat_display_names.get(stat_name, stat_name)
            # Format numeric values
            if isinstance(value, (int, float)):
                if value == int(value):
                    formatted_value = f"{int(value):,}"
                else:
                    formatted_value = f"{value:,.1f}"
            else:
                formatted_value = str(value)
            expandable_stats.append({'name': display_name, 'value': formatted_value})
    
    return {
        'mobile_stats': mobile_stats,
        'desktop_stats': desktop_stats,
        'expandable_stats': expandable_stats,
        'all_stats': mobile_stats + desktop_stats + expandable_stats
    }


@register.simple_tag
def team_record_display(team_records, team_id, show_zero=True):
    """
    Display team record in a nice format (e.g., "8-2" or "0-0").

    Args:
        team_records: Dictionary mapping team_id to (wins, losses) tuples
        team_id: The team ID to get record for
        show_zero: Whether to show "0-0" for teams with no games or hide it

    Returns:
        HTML string with formatted record or empty string
    """
    if not team_records or team_id not in team_records:
        return ""

    try:
        wins, losses = team_records[team_id]

        # Ensure wins and losses are valid numbers
        if not isinstance(wins, int) or not isinstance(losses, int):
            return ""

        # Don't show record if both are zero and show_zero is False
        if not show_zero and wins == 0 and losses == 0:
            return ""

        return mark_safe(f'<span class="text-xs text-base-content/60">{wins}-{losses}</span>')

    except (ValueError, TypeError, IndexError):
        # Handle any unexpected data format issues
        return ""


@register.filter
def add_attrs(field, attrs):
    """
    Update a form field widget with additional HTML attributes.
    Usage: {{ form.field|add_attrs:"class=input primary,placeholder=Enter value" }}
    """
    if not hasattr(field, "as_widget"):
        return field

    attrs_dict = {}
    if attrs:
        for attr in attrs.split(","):
            attr = attr.strip()
            if not attr:
                continue
            if "=" in attr:
                key, value = attr.split("=", 1)
                attrs_dict[key.strip()] = value.strip()
            else:
                attrs_dict[attr] = True

    widget_attrs = field.field.widget.attrs.copy()
    widget_attrs.update(attrs_dict)

    return field.as_widget(attrs=widget_attrs)