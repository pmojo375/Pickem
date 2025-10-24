"""
Management command to update team win/loss records based on completed games.
Resets all team records to 0-0 and then recalculates based on game outcomes.
"""
import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from cfb.models import Season, Team, Game

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update team win/loss records based on completed games for a given season'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            required=True,
            help='Season year to update team records for (e.g., 2025)',
        )
        parser.add_argument(
            '--no-confirm',
            action='store_true',
            help='Skip confirmation prompt',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        no_confirm = options['no_confirm']
        dry_run = options['dry_run']

        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        # Get all teams for this season
        teams = Team.objects.filter(season=season)
        team_count = teams.count()

        if team_count == 0:
            raise CommandError(f'No teams found for season {season_year}')

        # Get all completed games for this season
        completed_games = Game.objects.filter(
            season=season,
            is_final=True,
            home_score__isnull=False,
            away_score__isnull=False
        ).select_related('home_team', 'away_team')

        completed_game_count = completed_games.count()

        self.stdout.write(
            self.style.WARNING(
                f'About to update team records for {season.year}'
            )
        )
        self.stdout.write(f'  Teams to update: {team_count}')
        self.stdout.write(f'  Completed games: {completed_game_count}')

        if dry_run:
            self.stdout.write(self.style.SUCCESS('\nDRY RUN - No changes will be made'))

        if not no_confirm and not dry_run:
            response = input('Are you sure? (yes/no): ')
            if response.lower() != 'yes':
                self.stdout.write(self.style.ERROR('Cancelled'))
                return

        # Calculate what would happen without making changes
        self.stdout.write(self.style.SUCCESS('\nCalculating records from completed games...'))

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
            self.stdout.write(self.style.SUCCESS(f'\nDRY RUN Results:'))
            self.stdout.write(f'  Games processed: {games_processed}')
            self.stdout.write(f'  Teams to update: {len(updated_teams)}')

            # Show what records would be updated
            if wins_to_add or losses_to_add:
                self.stdout.write(self.style.SUCCESS('\nRecord changes that would be made:'))
                for team_id in sorted(updated_teams):
                    team = teams.get(id=team_id)
                    new_wins = wins_to_add.get(team_id, 0)
                    new_losses = losses_to_add.get(team_id, 0)
                    self.stdout.write(f'  {team.name}: 0-0 → {new_wins}-{new_losses}')
            return

        # Reset all team records to 0-0
        self.stdout.write(self.style.SUCCESS('\nResetting all team records to 0-0...'))
        reset_count = teams.update(record_wins=0, record_losses=0)
        self.stdout.write(f'  Reset {reset_count} team records')

        # Apply the calculated changes
        self.stdout.write(self.style.SUCCESS('Updating team records...'))

        for team_id, wins in wins_to_add.items():
            Team.objects.filter(id=team_id).update(record_wins=wins)

        for team_id, losses in losses_to_add.items():
            Team.objects.filter(id=team_id).update(record_losses=losses)

        self.stdout.write(
            self.style.SUCCESS(f'\n✓ Record update complete!')
        )
        self.stdout.write(f'  Games processed: {games_processed}')
        self.stdout.write(f'  Teams updated: {len(updated_teams)}')

        # Show some sample records
        sample_teams = Team.objects.filter(season=season).order_by('-record_wins')[:10]
        if sample_teams.exists():
            self.stdout.write(self.style.SUCCESS('\nTop 10 teams by wins:'))
            for team in sample_teams:
                self.stdout.write(f'  {team.name}: {team.record_wins}-{team.record_losses}')

        logger.info(f"Updated team records for season {season_year}: {games_processed} games processed, {len(updated_teams)} teams updated")
