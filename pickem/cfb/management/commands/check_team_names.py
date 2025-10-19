"""
Management command to check team names in database
"""
from django.core.management.base import BaseCommand
from cfb.models import Team, Season


class Command(BaseCommand):
    help = 'List all team names for a season'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            default=2025,
            help='Season year (default: 2025)',
        )
        parser.add_argument(
            '--search',
            type=str,
            help='Search for teams containing this term',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        search_term = options['search']

        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Season {season_year} not found'))
            return

        if search_term:
            teams = Team.objects.filter(season=season, name__icontains=search_term).order_by('name')
            self.stdout.write(self.style.SUCCESS(f'\nTeams matching "{search_term}":'))
        else:
            teams = Team.objects.filter(season=season).order_by('name')
            self.stdout.write(self.style.SUCCESS(f'\nAll teams in season {season_year}:'))

        for team in teams:
            # Show team name with encoding info
            self.stdout.write(f'  {team.name} (encoding check: {team.name.encode("utf-8")})') 

        self.stdout.write(self.style.SUCCESS(f'\nTotal: {teams.count()} teams'))
