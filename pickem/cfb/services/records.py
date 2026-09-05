"""
Service functions for managing team records and win/loss calculations.
"""
import logging
from collections import defaultdict

from django.db import transaction
from cfb.models import Season, Team, Game

logger = logging.getLogger(__name__)


def _home_spread_for_ats(game):
    """Prefer current (closing) spread; fall back to opening."""
    if game.current_home_spread is not None:
        return float(game.current_home_spread)
    if game.opening_home_spread is not None:
        return float(game.opening_home_spread)
    return None


def update_team_records(season_year, dry_run=False):
    """
    Update team win/loss and ATS records based on completed games for a given season.
    
    Args:
        season_year (int): The year of the season to update records for
        dry_run (bool): If True, calculate changes without applying them
        
    Returns:
        dict: Summary of the update operation containing:
            - games_processed (int): Number of games processed for W-L
            - ats_games_processed (int): Number of games processed for ATS
            - teams_updated (int): Number of teams that had records updated
            - wins_to_add / losses_to_add (dict): Team ID -> count
            - ats_wins_to_add / ats_losses_to_add / ats_pushes_to_add (dict)
            - updated_teams (set): Set of team IDs that were updated
            
    Raises:
        Season.DoesNotExist: If the specified season doesn't exist
        ValueError: If no teams found for the season
    """
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        raise Season.DoesNotExist(f'Season {season_year} does not exist')

    teams = Team.objects.filter(season=season)
    team_count = teams.count()

    if team_count == 0:
        raise ValueError(f'No teams found for season {season_year}')

    completed_games = Game.objects.filter(
        season=season,
        is_final=True,
        home_score__isnull=False,
        away_score__isnull=False
    ).select_related('home_team', 'away_team')

    completed_game_count = completed_games.count()

    logger.info(
        f'Updating team records for {season.year}: '
        f'{team_count} teams, {completed_game_count} completed games'
    )

    wins_to_add = defaultdict(int)
    losses_to_add = defaultdict(int)
    ats_wins_to_add = defaultdict(int)
    ats_losses_to_add = defaultdict(int)
    ats_pushes_to_add = defaultdict(int)
    updated_teams = set()
    games_processed = 0
    ats_games_processed = 0

    for game in completed_games:
        # Straight-up W-L (ties ignored)
        if game.home_score > game.away_score:
            wins_to_add[game.home_team_id] += 1
            losses_to_add[game.away_team_id] += 1
            updated_teams.add(game.home_team_id)
            updated_teams.add(game.away_team_id)
            games_processed += 1
        elif game.away_score > game.home_score:
            wins_to_add[game.away_team_id] += 1
            losses_to_add[game.home_team_id] += 1
            updated_teams.add(game.home_team_id)
            updated_teams.add(game.away_team_id)
            games_processed += 1

        # ATS from game spread (home perspective)
        home_spread = _home_spread_for_ats(game)
        if home_spread is None:
            continue

        actual_margin = game.home_score - game.away_score
        cover_margin = actual_margin + home_spread

        if cover_margin > 0:
            ats_wins_to_add[game.home_team_id] += 1
            ats_losses_to_add[game.away_team_id] += 1
        elif cover_margin < 0:
            ats_wins_to_add[game.away_team_id] += 1
            ats_losses_to_add[game.home_team_id] += 1
        else:
            ats_pushes_to_add[game.home_team_id] += 1
            ats_pushes_to_add[game.away_team_id] += 1

        updated_teams.add(game.home_team_id)
        updated_teams.add(game.away_team_id)
        ats_games_processed += 1

    result = {
        'games_processed': games_processed,
        'ats_games_processed': ats_games_processed,
        'teams_updated': len(updated_teams),
        'wins_to_add': dict(wins_to_add),
        'losses_to_add': dict(losses_to_add),
        'ats_wins_to_add': dict(ats_wins_to_add),
        'ats_losses_to_add': dict(ats_losses_to_add),
        'ats_pushes_to_add': dict(ats_pushes_to_add),
        'updated_teams': updated_teams,
        'dry_run': dry_run,
    }

    if dry_run:
        logger.info(
            f'DRY RUN: Would process {games_processed} W-L games, '
            f'{ats_games_processed} ATS games, update {len(updated_teams)} teams'
        )
        return result

    # Reset + write must share one transaction so concurrent workers cannot
    # interleave resets and leave teams partially updated / deadlocked.
    with transaction.atomic():
        logger.info('Resetting all team W-L and ATS records to 0')
        reset_count = teams.update(
            record_wins=0,
            record_losses=0,
            ats_wins=0,
            ats_losses=0,
            ats_pushes=0,
        )
        logger.info(f'Reset {reset_count} team records')

        logger.info('Updating team records with calculated W-L and ATS')
        for team_id in updated_teams:
            Team.objects.filter(id=team_id).update(
                record_wins=wins_to_add.get(team_id, 0),
                record_losses=losses_to_add.get(team_id, 0),
                ats_wins=ats_wins_to_add.get(team_id, 0),
                ats_losses=ats_losses_to_add.get(team_id, 0),
                ats_pushes=ats_pushes_to_add.get(team_id, 0),
            )

    logger.info(
        f"Updated team records for season {season_year}: "
        f"{games_processed} W-L games, {ats_games_processed} ATS games, "
        f"{len(updated_teams)} teams updated"
    )
    return result


def get_team_record_summary(season_year, limit=10):
    """
    Get a summary of team records for a given season.
    
    Args:
        season_year (int): The year of the season to get records for
        limit (int): Maximum number of teams to return (ordered by wins)
        
    Returns:
        list: List of team objects with their records, ordered by wins descending
    """
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        raise Season.DoesNotExist(f'Season {season_year} does not exist')
    
    return Team.objects.filter(season=season).order_by('-record_wins')[:limit]
