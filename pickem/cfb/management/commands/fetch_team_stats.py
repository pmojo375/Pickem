"""
Management command to fetch team season statistics from CFBD API for the current week.
This is a test command to verify the API response before implementing full integration.
"""
from django.core.management.base import BaseCommand, CommandError

from cfb.models import Season
from cfb.services.cfbd_api import get_cfbd_client
from cfb.services.schedule import get_current_week


class Command(BaseCommand):
    help = 'Fetch team season statistics from CFBD API for the current week'

    def add_arguments(self, parser):
        parser.add_argument(
            '--week',
            type=int,
            help='Specific week number (optional, default: current week)',
        )
        parser.add_argument(
            '--season',
            type=int,
            help='Season year (optional, default: active season)',
        )

    def handle(self, *args, **options):
        week = options.get('week')
        season_year = options.get('season')
        
        # Get current week if not specified
        if week is None:
            current_week = get_current_week()
            if not current_week:
                raise CommandError('No current week found. Please specify --week and --season.')
            
            week = current_week.number
            if season_year is None:
                season_year = current_week.season.year
            
            self.stdout.write(
                self.style.WARNING(
                    f'Using current week as end week: Week {week} of {season_year}'
                )
            )
        else:
            if season_year is None:
                # Try to get active season
                try:
                    season = Season.objects.filter(is_active=True).first()
                    if not season:
                        raise CommandError('No active season found. Please specify --season.')
                    season_year = season.year
                except Season.DoesNotExist:
                    raise CommandError('No active season found. Please specify --season.')
        
        # end_week will be week - 1 (last completed week)
        last_completed_week = max(0, week - 1)
        
        self.stdout.write(
            self.style.WARNING(
                f'Fetching cumulative team stats from CFBD for {season_year} (weeks 0-{last_completed_week}, current week: {week})...'
            )
        )
        
        # Fetch stats from CFBD (cumulative from week 0 through end_week)
        cfbd_client = get_cfbd_client()
        stats_data = cfbd_client.fetch_season_stats(
            year=season_year,
            end_week=week
        )
        
        if not stats_data:
            raise CommandError('Failed to fetch team stats data from CFBD API')
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully fetched {len(stats_data)} team stat records from CFBD'
            )
        )
        
        # Show a sample of the data structure
        if stats_data:
            self.stdout.write('\nSample data structure (first team):')
            self.stdout.write(self.style.SUCCESS(str(stats_data[0])))
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nJSON response saved to: pickem/cfbd_data/stats_season_*.json'
            )
        )

