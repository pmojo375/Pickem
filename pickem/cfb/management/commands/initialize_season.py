"""
Management command to initialize a season with teams and games from CFBD API.
"""
from django.core.management.base import BaseCommand, CommandError
from cfb.tasks import initialize_season, pull_season_teams, pull_season_games
from cfb.models import Season


class Command(BaseCommand):
    help = 'Initialize a season by pulling teams and games from CFBD API'

    def add_arguments(self, parser):
        parser.add_argument(
            'year',
            type=int,
            help='Season year to initialize (e.g., 2024)'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-pull even if already pulled'
        )
        parser.add_argument(
            '--teams-only',
            action='store_true',
            help='Only pull teams, skip games'
        )
        parser.add_argument(
            '--games-only',
            action='store_true',
            help='Only pull games, skip teams'
        )
        parser.add_argument(
            '--async',
            action='store_true',
            dest='run_async',
            help='Run as Celery task (asynchronously)'
        )

    def handle(self, *args, **options):
        year = options['year']
        force = options['force']
        teams_only = options['teams_only']
        games_only = options['games_only']
        run_async = options['run_async']

        self.stdout.write(f"Initializing season {year}...")

        # Get or create season
        season, created = Season.objects.get_or_create(
            year=year,
            defaults={'name': f'{year} Season', 'is_active': False}
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created new season: {year}"))
        else:
            self.stdout.write(f"Season {year} already exists")

        # Check status
        self.stdout.write(f"  Teams pulled: {season.teams_pulled}")
        self.stdout.write(f"  Games pulled: {season.games_pulled}")
        
        if not force and season.teams_pulled and season.games_pulled:
            self.stdout.write(
                self.style.WARNING(
                    "Season already initialized. Use --force to re-pull."
                )
            )
            return

        # Run tasks
        if run_async:
            self.stdout.write("Running tasks asynchronously...")
            if teams_only:
                result = pull_season_teams.delay(year, force=force)
                self.stdout.write(f"Task ID: {result.id}")
            elif games_only:
                result = pull_season_games.delay(year, force=force)
                self.stdout.write(f"Task ID: {result.id}")
            else:
                result = initialize_season.delay(year, force=force)
                self.stdout.write(f"Task ID: {result.id}")
            self.stdout.write(
                self.style.SUCCESS(
                    "Tasks queued. Check Celery logs for progress."
                )
            )
        else:
            self.stdout.write("Running tasks synchronously...")
            try:
                if teams_only:
                    pull_season_teams(year, force=force)
                elif games_only:
                    pull_season_games(year, force=force)
                else:
                    initialize_season(year, force=force)
                
                # Refresh and show results
                season.refresh_from_db()
                team_count = season.teams.count()
                game_count = season.games.count()
                
                self.stdout.write(self.style.SUCCESS("\nInitialization complete!"))
                self.stdout.write(f"  Teams: {team_count}")
                self.stdout.write(f"  Games: {game_count}")
                self.stdout.write(f"  Teams pulled: {season.teams_pulled}")
                self.stdout.write(f"  Games pulled: {season.games_pulled}")
                
            except Exception as e:
                raise CommandError(f"Error initializing season: {e}")

