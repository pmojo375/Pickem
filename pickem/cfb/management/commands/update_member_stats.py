"""
Management command to recalculate member week and season statistics.
Useful for catching up after migrations or fixing scoring issues.
"""
from django.core.management.base import BaseCommand, CommandError
from cfb.models import Season, Game
from cfb.services.scoring import recalculate_all_member_stats, update_member_week_for_game


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
        parser.add_argument(
            '--grade-picks',
            action='store_true',
            help='Grade all picks for final games before calculating stats',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        no_confirm = options['no_confirm']
        grade_picks = options['grade_picks']

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

        # Grade picks for final games if requested
        if grade_picks:
            self.stdout.write(self.style.SUCCESS('\nGrading picks for final games...'))
            final_games = Game.objects.filter(
                season=season,
                is_final=True
            ).select_related('home_team', 'away_team')
            
            graded_count = 0
            for game in final_games:
                result = update_member_week_for_game(game)
                if result > 0:
                    graded_count += result
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'  Graded {result} picks for {game.away_team.name} @ {game.home_team.name}'
                        )
                    )
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Graded {graded_count} total picks\n')
            )

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
