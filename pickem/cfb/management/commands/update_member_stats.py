"""
Management command to recalculate member week and season statistics.
Useful for catching up after migrations or fixing scoring issues.
"""
from django.core.management.base import BaseCommand, CommandError
from cfb.models import Season
from cfb.services.scoring import recalculate_all_member_stats


class Command(BaseCommand):
    help = 'Recalculate member week and season statistics for a given season'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            required=True,
            help='Season year to recalculate stats for (e.g., 2025)',
        )
        parser.add_argument(
            '--no-confirm',
            action='store_true',
            help='Skip confirmation prompt',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        no_confirm = options['no_confirm']

        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        self.stdout.write(
            self.style.WARNING(
                f'About to recalculate all member statistics for {season.year}'
            )
        )
        
        if not no_confirm:
            response = input('Are you sure? (yes/no): ')
            if response.lower() != 'yes':
                self.stdout.write(self.style.ERROR('Cancelled'))
                return

        self.stdout.write(self.style.SUCCESS('Starting recalculation...'))

        # Recalculate all stats
        result = recalculate_all_member_stats(season)

        # Display results
        self.stdout.write(self.style.SUCCESS('\n✓ Recalculation complete!'))
        self.stdout.write(f'  Leagues processed: {result["leagues_processed"]}')
        self.stdout.write(f'  MemberWeek records updated: {result["member_weeks_updated"]}')
        self.stdout.write(f'  MemberSeason records updated: {result["member_seasons_updated"]}')

        if result['errors']:
            self.stdout.write(self.style.ERROR('\nErrors encountered:'))
            for error in result['errors']:
                self.stdout.write(f'  - {error}')
        else:
            self.stdout.write(self.style.SUCCESS('\nNo errors!'))
