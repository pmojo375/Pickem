"""
Service functions for managing team records and win/loss calculations.
"""
import logging
from django.db import transaction
from cfb.models import Season, Team, Game

logger = logging.getLogger(__name__)


def update_team_records(season_year, dry_run=False):
    """
    Update team win/loss records based on completed games for a given season.
    
    Args:
        season_year (int): The year of the season to update records for
        dry_run (bool): If True, calculate changes without applying them
        
    Returns:
        dict: Summary of the update operation containing:
            - games_processed (int): Number of games processed
            - teams_updated (int): Number of teams that had records updated
            - wins_to_add (dict): Team ID -> wins to add mapping
            - losses_to_add (dict): Team ID -> losses to add mapping
            - updated_teams (set): Set of team IDs that were updated
            
    Raises:
        Season.DoesNotExist: If the specified season doesn't exist
        ValueError: If no teams found for the season
    """
    # Verify season exists
    try:
        season = Season.objects.get(year=season_year)
    except Season.DoesNotExist:
        raise Season.DoesNotExist(f'Season {season_year} does not exist')

    # Get all teams for this season
    teams = Team.objects.filter(season=season)
    team_count = teams.count()

    if team_count == 0:
        raise ValueError(f'No teams found for season {season_year}')

    # Get all completed games for this season
    completed_games = Game.objects.filter(
        season=season,
        is_final=True,
        home_score__isnull=False,
        away_score__isnull=False
    ).select_related('home_team', 'away_team')

    completed_game_count = completed_games.count()

    logger.info(f'Updating team records for {season.year}: {team_count} teams, {completed_game_count} completed games')

    # Calculate what would happen without making changes
    updated_teams = set()
    games_processed = 0
    wins_to_add = {}
    losses_to_add = {}

    for game in completed_games:
        # Determine winner
        if game.home_score > game.away_score:
            # Home team wins
            winner_id = game.home_team.id
            loser_id = game.away_team.id
        elif game.away_score > game.home_score:
            # Away team wins
            winner_id = game.away_team.id
            loser_id = game.home_team.id
        else:
            # Tie game - no record change needed
            continue

        # Track changes
        wins_to_add[winner_id] = wins_to_add.get(winner_id, 0) + 1
        losses_to_add[loser_id] = losses_to_add.get(loser_id, 0) + 1
        updated_teams.add(winner_id)
        updated_teams.add(loser_id)
        games_processed += 1

    if dry_run:
        logger.info(f'DRY RUN: Would process {games_processed} games and update {len(updated_teams)} teams')
        return {
            'games_processed': games_processed,
            'teams_updated': len(updated_teams),
            'wins_to_add': wins_to_add,
            'losses_to_add': losses_to_add,
            'updated_teams': updated_teams,
            'dry_run': True
        }

    # Reset all team records to 0-0
    logger.info('Resetting all team records to 0-0')
    reset_count = teams.update(record_wins=0, record_losses=0)
    logger.info(f'Reset {reset_count} team records')

    # Apply the calculated changes
    logger.info('Updating team records with calculated wins and losses')
    
    with transaction.atomic():
        for team_id, wins in wins_to_add.items():
            Team.objects.filter(id=team_id).update(record_wins=wins)

        for team_id, losses in losses_to_add.items():
            Team.objects.filter(id=team_id).update(record_losses=losses)

    logger.info(f"Updated team records for season {season_year}: {games_processed} games processed, {len(updated_teams)} teams updated")
    
    return {
        'games_processed': games_processed,
        'teams_updated': len(updated_teams),
        'wins_to_add': wins_to_add,
        'losses_to_add': losses_to_add,
        'updated_teams': updated_teams,
        'dry_run': False
    }


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
