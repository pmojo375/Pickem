"""
Management command to lock the current (most updated) spreads for a week.

Sets locked_home_spread / locked_away_spread on LeagueGame from each Game's
current_home_spread / current_away_spread.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cfb.models import LeagueGame, Season, Week


class Command(BaseCommand):
    help = 'Lock current spreads onto league games for a given week'

    def add_arguments(self, parser):
        parser.add_argument(
            'season',
            type=int,
            help='Season year (e.g., 2025)',
        )
        parser.add_argument(
            'week',
            type=int,
            help='Week number (e.g., 1, 2, 3...)',
        )
        parser.add_argument(
            '--season-type',
            type=str,
            default='regular',
            choices=['regular', 'postseason'],
            help='Season type (default: regular)',
        )
        parser.add_argument(
            '--league',
            type=str,
            help='Specific league to update (optional - updates all leagues if not specified)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite spreads that are already locked',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without making changes',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        week_num = options['week']
        season_type = options['season_type']
        league_name = options.get('league')
        force = options['force']
        dry_run = options['dry_run']

        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        try:
            week = Week.objects.get(
                season=season,
                number=week_num,
                season_type=season_type,
            )
        except Week.DoesNotExist:
            raise CommandError(
                f'Week {week_num} ({season_type}) does not exist for season {season_year}'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Locking current spreads for {season_year} {season_type} week {week_num}'
            )
        )

        league_games = LeagueGame.objects.filter(
            game__week=week,
            is_active=True,
        ).select_related('game', 'game__home_team', 'game__away_team', 'league')

        if league_name:
            league_games = league_games.filter(league__name__iexact=league_name)

        if not force:
            league_games = league_games.filter(locked_home_spread__isnull=True)

        if not league_games.exists():
            self.stdout.write(
                self.style.WARNING('No matching league games found to lock')
            )
            return

        locked_count = 0
        skipped_no_spread = 0
        skipped_already_locked = 0

        with transaction.atomic():
            for league_game in league_games:
                game = league_game.game

                if game.current_home_spread is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Skip (no current spread): {league_game.league.name} — {game}'
                        )
                    )
                    skipped_no_spread += 1
                    continue

                if (
                    not force
                    and league_game.locked_home_spread is not None
                ):
                    skipped_already_locked += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        self.style.WARNING(
                            f'[DRY RUN] Would lock {league_game.league.name}: {game}\n'
                            f'  Home: {league_game.locked_home_spread} → {game.current_home_spread}\n'
                            f'  Away: {league_game.locked_away_spread} → {game.current_away_spread}'
                        )
                    )
                    locked_count += 1
                    continue

                league_game.locked_home_spread = game.current_home_spread
                league_game.locked_away_spread = game.current_away_spread
                league_game.spread_locked_at = timezone.now()
                league_game.save(
                    update_fields=[
                        'locked_home_spread',
                        'locked_away_spread',
                        'spread_locked_at',
                    ]
                )
                locked_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Locked {league_game.league.name}: {game} '
                        f'({game.current_home_spread}/{game.current_away_spread})'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n=== Summary ===\n'
                f'Locked: {locked_count}\n'
                f'Skipped (no current spread): {skipped_no_spread}\n'
                f'Skipped (already locked): {skipped_already_locked}'
            )
        )

        if dry_run:
            self.stdout.write(
                self.style.WARNING('\n[DRY RUN MODE] - No changes were made')
            )
