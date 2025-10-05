"""
Management command to sync games from ESPN for a date range.
Useful for initial setup or backfilling missing games.
"""
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cfb.models import Season
from cfb.tasks import sync_games_from_espn


class Command(BaseCommand):
    help = 'Sync games from ESPN for a specific date range'

    def add_arguments(self, parser):
        parser.add_argument(
            'season_year',
            type=int,
            help='Season year (e.g., 2024)',
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date in YYYY-MM-DD format (default: 7 days ago)',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date in YYYY-MM-DD format (default: today)',
        )
        parser.add_argument(
            '--async',
            action='store_true',
            help='Run the task asynchronously via Celery',
        )

    def handle(self, *args, **options):
        season_year = options['season_year']
        
        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        # Parse dates
        if options['start_date']:
            try:
                start_date = datetime.strptime(options['start_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid start date format. Use YYYY-MM-DD')
        else:
            start_date = (timezone.now() - timedelta(days=7)).date()

        if options['end_date']:
            try:
                end_date = datetime.strptime(options['end_date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('Invalid end date format. Use YYYY-MM-DD')
        else:
            end_date = timezone.now().date()

        if start_date > end_date:
            raise CommandError('Start date must be before or equal to end date')

        self.stdout.write(
            self.style.WARNING(
                f'Syncing games for season {season_year} '
                f'from {start_date} to {end_date}'
            )
        )

        run_async = options['async']
        start_date_str = start_date.isoformat()
        end_date_str = end_date.isoformat()

        if run_async:
            # Queue the task in Celery
            result = sync_games_from_espn.delay(
                season_year,
                start_date_str,
                end_date_str
            )
            self.stdout.write(
                self.style.SUCCESS(f'Task queued with ID: {result.id}')
            )
        else:
            # Run synchronously
            sync_games_from_espn(season_year, start_date_str, end_date_str)
            self.stdout.write(
                self.style.SUCCESS('Game sync completed')
            )

