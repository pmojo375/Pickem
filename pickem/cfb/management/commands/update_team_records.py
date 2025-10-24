"""
Management command to update team win/loss records based on completed games.
Resets all team records to 0-0 and then recalculates based on game outcomes.
"""
import logging
from django.core.management.base import BaseCommand, CommandError
from cfb.models import Season, Team
from cfb.services.records import update_team_records, get_team_record_summary

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

        # Get completed games count for display
        from cfb.models import Game
        completed_games = Game.objects.filter(
            season=season,
            is_final=True,
            home_score__isnull=False,
            away_score__isnull=False
        )
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

        # Use the service function to update records
        try:
            self.stdout.write(self.style.SUCCESS('\nCalculating records from completed games...'))
            result = update_team_records(season_year, dry_run=dry_run)
            
            if dry_run:
                self.stdout.write(self.style.SUCCESS(f'\nDRY RUN Results:'))
                self.stdout.write(f'  Games processed: {result["games_processed"]}')
                self.stdout.write(f'  Teams to update: {result["teams_updated"]}')

                # Show what records would be updated
                if result["wins_to_add"] or result["losses_to_add"]:
                    self.stdout.write(self.style.SUCCESS('\nRecord changes that would be made:'))
                    for team_id in sorted(result["updated_teams"]):
                        team = teams.get(id=team_id)
                        new_wins = result["wins_to_add"].get(team_id, 0)
                        new_losses = result["losses_to_add"].get(team_id, 0)
                        self.stdout.write(f'  {team.name}: 0-0 → {new_wins}-{new_losses}')
                return

            self.stdout.write(
                self.style.SUCCESS(f'\n✓ Record update complete!')
            )
            self.stdout.write(f'  Games processed: {result["games_processed"]}')
            self.stdout.write(f'  Teams updated: {result["teams_updated"]}')

            # Show some sample records
            sample_teams = get_team_record_summary(season_year, limit=10)
            if sample_teams:
                self.stdout.write(self.style.SUCCESS('\nTop 10 teams by wins:'))
                for team in sample_teams:
                    self.stdout.write(f'  {team.name}: {team.record_wins}-{team.record_losses}')

        except Exception as e:
            raise CommandError(f'Error updating team records: {str(e)}')
