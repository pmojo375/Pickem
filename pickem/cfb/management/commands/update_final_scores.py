"""
Management command to update scores for final games that may have been missed.
Useful for backfilling or ensuring all final games have updated scores.
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from cfb.models import Season, Game, Week
from cfb.services.live import fetch_and_store_live_scores
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Update scores for final games that may have been missed'

    def add_arguments(self, parser):
        parser.add_argument(
            '--season',
            type=int,
            help='Season year to update scores for (e.g., 2025). If omitted, uses all seasons.',
            default=None
        )
        parser.add_argument(
            '--week',
            type=int,
            help='Specific week to update (optional). If omitted, updates all weeks.',
            default=None
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days in the past to look for games (default: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without actually updating'
        )

    def handle(self, *args, **options):
        season_year = options.get('season')
        week = options.get('week')
        days = options['days']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE - No changes will be saved"))

        # Build query to find games that may need score updates
        now = timezone.now()
        lookback_date = now - timezone.timedelta(days=days)

        games_query = Game.objects.filter(
            kickoff__gte=lookback_date,
            kickoff__lte=now
        ).select_related('home_team', 'away_team', 'week', 'season')

        # Apply season filter if provided
        if season_year:
            try:
                season = Season.objects.get(year=season_year)
                games_query = games_query.filter(season=season)
            except Season.DoesNotExist:
                raise CommandError(f'Season {season_year} does not exist')
        
        # Apply week filter if provided
        if week and season_year:
            try:
                week_obj = Week.objects.get(season=season, number=week)
                games_query = games_query.filter(week=week_obj)
            except Week.DoesNotExist:
                raise CommandError(f'Week {week} does not exist for season {season_year}')

        # Get games that have started
        games = games_query.filter(kickoff__lte=now)
        
        initial_count = games.count()
        
        if initial_count == 0:
            self.stdout.write(self.style.WARNING('No games found in the specified time range'))
            return

        self.stdout.write(
            f"Found {initial_count} games that have started "
            f"(looking back {days} days)"
        )

        # Show how many already have scores
        games_with_scores = games.filter(
            home_score__isnull=False,
            away_score__isnull=False,
            is_final=True
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  {games_with_scores.count()} games already marked final with scores"
            )
        )

        # Show how many need updating
        games_needing_update = games.exclude(
            home_score__isnull=False,
            away_score__isnull=False,
            is_final=True
        )
        self.stdout.write(
            self.style.WARNING(
                f"  {games_needing_update.count()} games need score updates"
            )
        )

        if not games_needing_update.exists():
            self.stdout.write(self.style.SUCCESS("All games already have final scores!"))
            return

        if dry_run:
            self.stdout.write("\nGames that would be updated:")
            for game in games_needing_update[:10]:
                score_str = "No score" if game.home_score is None else f"{game.away_score}-{game.home_score}"
                final_str = "FINAL" if game.is_final else "Not Final"
                self.stdout.write(
                    f"  {game.away_team.name} @ {game.home_team.name}: "
                    f"{score_str} ({final_str})"
                )
            if games_needing_update.count() > 10:
                self.stdout.write(f"  ... and {games_needing_update.count() - 10} more")
            return

        # Update scores using the existing fetch_and_store_live_scores function
        self.stdout.write("\nFetching scores from ESPN...")
        
        try:
            updated_count = fetch_and_store_live_scores()
            self.stdout.write(
                self.style.SUCCESS(f"✓ Successfully updated {updated_count} games")
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Error fetching scores: {e}")
            )
            raise CommandError(f'Failed to update scores: {e}')

        # Show summary of what was updated
        self.stdout.write("\n" + "="*60)
        self.stdout.write(self.style.SUCCESS("Update Summary:"))
        self.stdout.write(f"  Total games checked: {initial_count}")
        
        # Refresh to get updated counts
        games_with_scores_after = Game.objects.filter(
            pk__in=games.values_list('pk', flat=True),
            home_score__isnull=False,
            away_score__isnull=False,
            is_final=True
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"  Games now marked final with scores: {games_with_scores_after.count()}"
            )
        )
        
        self.stdout.write(self.style.SUCCESS("✓ Score update complete!"))

