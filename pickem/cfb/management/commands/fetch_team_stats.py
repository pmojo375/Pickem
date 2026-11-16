"""
Management command to fetch team season statistics from CFBD API for the current schedule_period.
This is a test command to verify the API response before implementing full integration.
"""
from django.core.management.base import BaseCommand, CommandError

from cfb.models import Season
from cfb.services.cfbd_api import get_cfbd_client
from cfb.services.schedule import get_current_period


class Command(BaseCommand):
    help = 'Fetch team season statistics from CFBD API for the current schedule_period'

    def add_arguments(self, parser):
        parser.add_argument(
            '--period', '--week', dest='schedule_period',
            type=int,
            help='Specific schedule_period number (optional, default: current schedule_period)',
        )
        parser.add_argument(
            '--season',
            type=int,
            help='Season year (optional, default: active season)',
        )

    def handle(self, *args, **options):
        schedule_period = options.get('schedule_period')
        season_year = options.get('season')
        
        # Get current schedule_period if not specified
        if schedule_period is None:
            current_period = get_current_period()
            if not current_period:
                raise CommandError('No current schedule_period found. Please specify --schedule_period and --season.')
            
            schedule_period = current_period.number
            if season_year is None:
                season_year = current_period.season.year
            
            self.stdout.write(
                self.style.WARNING(
                    f'Using current schedule_period as end schedule_period: SchedulePeriod {schedule_period} of {season_year}'
                )
            )
        else:
            if season_year is None:
                # Try to get active season
                try:
                    season = Season.objects.filter(sport_id="college-football", is_active=True).first()
                    if not season:
                        raise CommandError('No active season found. Please specify --season.')
                    season_year = season.year
                except Season.DoesNotExist:
                    raise CommandError('No active season found. Please specify --season.')
        
        # end_period will be schedule_period - 1 (last completed schedule_period)
        last_completed_period = max(0, schedule_period - 1)
        
        self.stdout.write(
            self.style.WARNING(
                f'Fetching cumulative team stats from CFBD for {season_year} (schedule_periods 0-{last_completed_period}, current schedule_period: {schedule_period})...'
            )
        )
        
        # Fetch stats from CFBD (cumulative from schedule_period 0 through end_period)
        cfbd_client = get_cfbd_client()
        stats_data = cfbd_client.fetch_season_stats(
            year=season_year,
            end_week=schedule_period
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

