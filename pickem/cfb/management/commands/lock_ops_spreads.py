"""
Management command to lock OPS imported spreads on LeagueGame records.

Takes GameSpread records with source "OPS Import" and uses them to set
the locked spreads on all related LeagueGame records.
"""
import logging
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from cfb.models import GameSpread, LeagueGame, Season

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Lock OPS imported spreads on all related league games'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            default=2025,
            help='Season year (default: 2025)',
        )
        parser.add_argument(
            '--league',
            type=str,
            help='Specific league to update (optional - updates all leagues if not specified)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        league_name = options['league']
        dry_run = options['dry_run']

        try:
            season = Season.objects.get(year=season_year)
            self.stdout.write(
                self.style.SUCCESS(f'Using season: {season}')
            )
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        # Get OPS imported spreads
        ops_spreads = GameSpread.objects.filter(
            source='OPS Import',
            game__season=season
        ).select_related('game')

        if not ops_spreads.exists():
            self.stdout.write(
                self.style.WARNING(f'No OPS imported spreads found for season {season_year}')
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f'\nFound {ops_spreads.count()} OPS imported spreads')
        )

        updated_count = 0
        skipped_count = 0
        error_count = 0

        with transaction.atomic():
            for spread in ops_spreads:
                try:
                    # Find all LeagueGame records for this game
                    league_games = LeagueGame.objects.filter(game=spread.game)

                    if league_name:
                        league_games = league_games.filter(league__name__iexact=league_name)

                    if not league_games.exists():
                        self.stdout.write(
                            self.style.WARNING(
                                f'No league games found for {spread.game} (spread: {spread.home_spread}/{spread.away_spread})'
                            )
                        )
                        skipped_count += 1
                        continue

                    for league_game in league_games:
                        if dry_run:
                            self.stdout.write(
                                self.style.WARNING(
                                    f'[DRY RUN] Would update {league_game.league.name}: {spread.game}\n'
                                    f'  Home spread: {league_game.locked_home_spread} → {spread.home_spread}\n'
                                    f'  Away spread: {league_game.locked_away_spread} → {spread.away_spread}'
                                )
                            )
                        else:
                            # Lock the spreads
                            league_game.locked_home_spread = spread.home_spread
                            league_game.locked_away_spread = spread.away_spread
                            league_game.spread_locked_at = spread.timestamp
                            league_game.save(
                                update_fields=[
                                    'locked_home_spread',
                                    'locked_away_spread',
                                    'spread_locked_at'
                                ]
                            )
                            updated_count += 1
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f'Updated {league_game.league.name}: {spread.game}\n'
                                    f'  Home: {spread.home_spread}, Away: {spread.away_spread}'
                                )
                            )

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f'Error processing spread for {spread.game}: {str(e)}')
                    )
                    error_count += 1
                    continue

        self.stdout.write(
            self.style.SUCCESS(
                f'\n=== Summary ===\n'
                f'Updated: {updated_count}\n'
                f'Skipped: {skipped_count}\n'
                f'Errors: {error_count}'
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\n[DRY RUN MODE] - No changes were made')
            )
