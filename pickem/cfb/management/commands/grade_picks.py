"""
Management command to grade all picks for games that are marked as final.

Looks at all games marked is_final=True and grades their picks according to league rules.
This is useful for ensuring all finished games have their picks properly scored.
"""
import logging
from django.core.management.base import BaseCommand, CommandError
from cfb.models import Game, Season, Week
from cfb.services.scoring import update_member_week_for_game

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Grade all picks for games that are marked as final'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            help='Season year to grade picks for (optional - grades all if not specified)',
        )
        parser.add_argument(
            '--week',
            type=int,
            help='Specific week number to grade (requires --season)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be graded without making changes',
        )

    def handle(self, *args, **options):
        season_year = options.get('season')
        week_num = options.get('week')
        dry_run = options['dry_run']

        # Build query
        games_query = Game.objects.filter(is_final=True)

        if season_year:
            try:
                season = Season.objects.get(year=season_year)
                games_query = games_query.filter(season=season)
                self.stdout.write(self.style.SUCCESS(f'Grading picks for season {season_year}'))
            except Season.DoesNotExist:
                raise CommandError(f'Season {season_year} does not exist')

            if week_num:
                try:
                    week = Week.objects.get(season=season, number=week_num)
                    games_query = games_query.filter(week=week)
                    self.stdout.write(self.style.SUCCESS(f'Grading picks for week {week_num}'))
                except Week.DoesNotExist:
                    raise CommandError(f'Week {week_num} does not exist for season {season_year}')
        else:
            self.stdout.write(self.style.SUCCESS('Grading picks for all final games'))

        final_games = games_query.select_related('home_team', 'away_team', 'week')
        game_count = final_games.count()

        if not game_count:
            self.stdout.write(self.style.WARNING('No final games found to grade'))
            return

        self.stdout.write(f'\nFound {game_count} final games to process\n')

        picked_count = 0
        graded_count = 0
        skipped_count = 0
        error_count = 0

        for game in final_games:
            try:
                game_str = f'{game.away_team.name} @ {game.home_team.name}'
                
                if dry_run:
                    # Just count picks without grading
                    pick_count = game.picks.filter(is_correct__isnull=True).count()
                    if pick_count > 0:
                        self.stdout.write(
                            self.style.WARNING(
                                f'[DRY RUN] Would grade {pick_count} picks for {game_str}'
                            )
                        )
                        picked_count += pick_count
                    else:
                        skipped_count += 1
                else:
                    # Grade picks for this game
                    ungraded_picks = game.picks.filter(is_correct__isnull=True)
                    if ungraded_picks.exists():
                        # Call the update function
                        result = update_member_week_for_game(game)
                        graded_count += result
                        picked_count += ungraded_picks.count()
                        
                        self.stdout.write(
                            self.style.SUCCESS(f'Graded {result} picks for {game_str}')
                        )
                    else:
                        skipped_count += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error grading picks for {game_str}: {str(e)}')
                )
                error_count += 1
                continue

        self.stdout.write(
            self.style.SUCCESS(
                f'\n=== Summary ===\n'
                f'Picks graded: {graded_count}\n'
                f'Games skipped (already graded): {skipped_count}\n'
                f'Errors: {error_count}'
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\n[DRY RUN MODE] - No picks were graded')
            )
