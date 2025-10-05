"""
Celery tasks for ESPN game score polling and updates.
Implements intelligent polling with Redis caching and dynamic intervals.
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import Game, Team, Season
from .services.espn_api import get_espn_client
from .services.live import grade_picks_for_game

logger = logging.getLogger(__name__)


class GameUpdateService:
    """Service for updating games with new data from ESPN."""

    @staticmethod
    def has_game_changed(game: Game, new_data: Dict[str, Any]) -> bool:
        """
        Check if any relevant fields have changed.
        
        Args:
            game: Existing Game instance
            new_data: New data from ESPN API
            
        Returns:
            True if any fields have changed
        """
        # Check score changes
        if new_data.get('home_score') != game.home_score:
            return True
        if new_data.get('away_score') != game.away_score:
            return True
        
        # Check status changes
        if new_data.get('is_final') != game.is_final:
            return True
        
        # Check period/clock changes
        if new_data.get('period') != game.quarter:
            return True
        if new_data.get('clock', '') != (game.clock or ''):
            return True
        
        return False

    @staticmethod
    def update_game_from_espn_data(game: Game, espn_data: Dict[str, Any]) -> bool:
        """
        Update a Game instance with ESPN data.
        
        Args:
            game: Game instance to update
            espn_data: Parsed ESPN game data
            
        Returns:
            True if game was updated, False otherwise
        """
        if not GameUpdateService.has_game_changed(game, espn_data):
            return False

        try:
            # Update live fields
            game.home_score = espn_data.get('home_score')
            game.away_score = espn_data.get('away_score')
            game.quarter = espn_data.get('period')
            game.clock = espn_data.get('clock', '')
            
            was_not_final = not game.is_final
            game.is_final = espn_data.get('is_final', False)
            
            game.save(update_fields=['home_score', 'away_score', 'quarter', 'clock', 'is_final'])
            
            logger.info(
                f"Updated game {game.external_id}: "
                f"{game.away_team.abbreviation or game.away_team.name} "
                f"{game.away_score or 0} @ "
                f"{game.home_team.abbreviation or game.home_team.name} "
                f"{game.home_score or 0} - "
                f"{'FINAL' if game.is_final else f'Q{game.quarter} {game.clock}'}"
            )
            
            # If game just became final, grade picks
            if game.is_final and was_not_final:
                graded = grade_picks_for_game(game)
                logger.info(f"Graded {graded} picks for completed game {game.external_id}")
            
            # Update Redis cache
            GameUpdateService.cache_game_data(game, espn_data)
            
            return True

        except Exception as e:
            logger.error(f"Error updating game {game.external_id}: {e}", exc_info=True)
            return False

    @staticmethod
    def cache_game_data(game: Game, espn_data: Optional[Dict[str, Any]] = None):
        """
        Cache game data in Redis for quick API access.
        
        Args:
            game: Game instance to cache
            espn_data: Optional ESPN data to include in cache
        """
        cache_key = f"{settings.REDIS_KEY_GAME_PREFIX}{game.external_id}"
        
        game_data = {
            'id': game.id,
            'external_id': game.external_id,
            'home_team': {
                'id': game.home_team.id,
                'name': game.home_team.name,
                'abbreviation': game.home_team.abbreviation,
                'logo_url': game.home_team.logo_url,
            },
            'away_team': {
                'id': game.away_team.id,
                'name': game.away_team.name,
                'abbreviation': game.away_team.abbreviation,
                'logo_url': game.away_team.logo_url,
            },
            'kickoff': game.kickoff.isoformat(),
            'home_score': game.home_score,
            'away_score': game.away_score,
            'quarter': game.quarter,
            'clock': game.clock,
            'is_final': game.is_final,
            'current_home_spread': float(game.current_home_spread) if game.current_home_spread else None,
            'current_away_spread': float(game.current_away_spread) if game.current_away_spread else None,
        }
        
        # Add ESPN-specific data if available
        if espn_data:
            game_data.update({
                'status_state': espn_data.get('status_state'),
                'status_detail': espn_data.get('status_detail'),
                'broadcast_network': espn_data.get('broadcast_network'),
            })
        
        cache.set(cache_key, game_data, timeout=settings.REDIS_KEY_GAME_CACHE_TTL)

    @staticmethod
    def find_or_create_team(
        season: Season,
        espn_id: str,
        name: str,
        abbreviation: str = ''
    ) -> Optional[Team]:
        """
        Find or create a team by ESPN ID.
        
        Args:
            season: Season for the team
            espn_id: ESPN team ID
            name: Team display name
            abbreviation: Team abbreviation
            
        Returns:
            Team instance or None on error
        """
        try:
            # Try to find by ESPN ID first
            team = Team.objects.filter(season=season, espn_id=espn_id).first()
            if team:
                return team
            
            # Try to find by name
            team = Team.objects.filter(season=season, name=name).first()
            if team:
                # Update ESPN ID if found by name
                team.espn_id = espn_id
                if abbreviation and not team.abbreviation:
                    team.abbreviation = abbreviation
                team.save(update_fields=['espn_id', 'abbreviation'])
                return team
            
            # Create new team
            team = Team.objects.create(
                season=season,
                name=name,
                espn_id=espn_id,
                abbreviation=abbreviation or name[:4].upper()
            )
            logger.info(f"Created new team: {name} (ESPN ID: {espn_id})")
            return team

        except Exception as e:
            logger.error(f"Error finding/creating team {name}: {e}", exc_info=True)
            return None


@shared_task(
    bind=True,
    name='cfb.tasks.poll_espn_scores',
    max_retries=3,
    default_retry_delay=60,
)
def poll_espn_scores(self):
    """
    Main task to poll ESPN for game scores and update database.
    Runs at intervals determined by live game state.
    """
    try:
        # Check last poll time to avoid duplicate work
        last_poll = cache.get(settings.REDIS_KEY_LAST_POLL)
        if last_poll:
            time_since_poll = (timezone.now().timestamp() - last_poll)
            if time_since_poll < 30:  # Minimum 30 seconds between polls
                logger.debug(f"Skipping poll, last poll was {time_since_poll:.1f}s ago")
                return

        # Record this poll attempt
        cache.set(settings.REDIS_KEY_LAST_POLL, timezone.now().timestamp(), timeout=300)

        logger.info("Starting ESPN score polling")
        
        # Get active season
        active_season = Season.objects.filter(is_active=True).first()
        if not active_season:
            logger.warning("No active season found")
            return

        # Determine date range for games to check
        now = timezone.now()
        start_date = now - timedelta(days=settings.GAME_CHECK_WINDOW_PAST)
        end_date = now + timedelta(days=settings.GAME_CHECK_WINDOW_FUTURE)

        # Get games from database that need checking
        games_to_check = Game.objects.filter(
            season=active_season,
            kickoff__gte=start_date,
            kickoff__lte=end_date
        ).select_related('home_team', 'away_team')

        if not games_to_check.exists():
            logger.info("No games in check window")
            return

        # Fetch data from ESPN
        espn_client = get_espn_client()
        espn_games = espn_client.fetch_games_in_range(start_date, end_date)

        if not espn_games:
            logger.warning("No games fetched from ESPN API")
            return

        # Update games
        updated_count = 0
        live_game_count = 0

        for game in games_to_check:
            if not game.external_id or game.external_id not in espn_games:
                continue

            espn_event = espn_games[game.external_id]
            parsed_data = espn_client.parse_game_data(espn_event)

            if not parsed_data:
                continue

            # Check if game is live
            if parsed_data.get('is_live'):
                live_game_count += 1

            # Update game if changed
            if GameUpdateService.update_game_from_espn_data(game, parsed_data):
                updated_count += 1

        # Update live state in Redis for polling interval adjustment
        cache.set(
            settings.REDIS_KEY_LIVE_STATE,
            {
                'has_live_games': live_game_count > 0,
                'live_game_count': live_game_count,
                'last_check': timezone.now().isoformat(),
            },
            timeout=settings.REDIS_KEY_LIVE_STATE_TTL
        )

        logger.info(
            f"ESPN polling complete: {updated_count} games updated, "
            f"{live_game_count} games live"
        )

    except Exception as exc:
        logger.error(f"Error in ESPN polling task: {exc}", exc_info=True)
        # Retry with exponential backoff
        raise self.retry(exc=exc)


@shared_task(name='cfb.tasks.adjust_polling_interval')
def adjust_polling_interval():
    """
    Adjust the polling interval based on whether games are currently live.
    This task runs frequently to check the live state.
    """
    try:
        live_state = cache.get(settings.REDIS_KEY_LIVE_STATE)
        
        if not live_state:
            # No state cached, assume normal polling
            return

        has_live_games = live_state.get('has_live_games', False)
        live_count = live_state.get('live_game_count', 0)

        # The actual polling happens in poll_espn_scores which self-regulates
        # via the REDIS_KEY_LAST_POLL cache key
        # This task just logs the current state for monitoring
        
        if has_live_games:
            logger.debug(f"Live games detected: {live_count} - Fast polling active")
        else:
            logger.debug("No live games - Normal polling active")

    except Exception as e:
        logger.error(f"Error adjusting polling interval: {e}", exc_info=True)


@shared_task(name='cfb.tasks.update_single_game')
def update_single_game(game_id: int):
    """
    Update a single game immediately (useful for manual triggers).
    
    Args:
        game_id: Database ID of the game to update
    """
    try:
        game = Game.objects.select_related('home_team', 'away_team').get(id=game_id)
        
        if not game.external_id:
            logger.warning(f"Game {game_id} has no external_id")
            return

        # Fetch from ESPN
        espn_client = get_espn_client()
        kickoff_date = timezone.localtime(game.kickoff)
        data = espn_client.fetch_scoreboard(date=kickoff_date)

        if not data or 'events' not in data:
            logger.warning(f"No data from ESPN for game {game_id}")
            return

        # Find the specific game
        for event in data['events']:
            if str(event.get('id')) == game.external_id:
                parsed_data = espn_client.parse_game_data(event)
                if parsed_data:
                    GameUpdateService.update_game_from_espn_data(game, parsed_data)
                    logger.info(f"Manually updated game {game_id}")
                return

        logger.warning(f"Game {game.external_id} not found in ESPN response")

    except Game.DoesNotExist:
        logger.error(f"Game {game_id} not found")
    except Exception as e:
        logger.error(f"Error updating game {game_id}: {e}", exc_info=True)


@shared_task(name='cfb.tasks.cleanup_old_game_cache')
def cleanup_old_game_cache():
    """
    Clean up old game cache entries from Redis.
    This is a periodic maintenance task.
    """
    try:
        # Get all games older than the check window
        cutoff_date = timezone.now() - timedelta(days=settings.GAME_CHECK_WINDOW_PAST + 1)
        
        old_games = Game.objects.filter(
            kickoff__lt=cutoff_date,
            is_final=True
        ).values_list('external_id', flat=True)

        deleted_count = 0
        for external_id in old_games:
            cache_key = f"{settings.REDIS_KEY_GAME_PREFIX}{external_id}"
            if cache.delete(cache_key):
                deleted_count += 1

        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} old game cache entries")

    except Exception as e:
        logger.error(f"Error cleaning up cache: {e}", exc_info=True)


@shared_task(name='cfb.tasks.sync_games_from_espn')
def sync_games_from_espn(season_year: int, start_date_str: str, end_date_str: str):
    """
    Sync games from ESPN for a specific date range.
    Useful for initial setup or filling in missing games.
    
    Args:
        season_year: Year of the season
        start_date_str: Start date in ISO format (YYYY-MM-DD)
        end_date_str: End date in ISO format (YYYY-MM-DD)
    """
    try:
        from datetime import datetime
        
        season = Season.objects.get(year=season_year)
        start_date = timezone.make_aware(datetime.fromisoformat(start_date_str))
        end_date = timezone.make_aware(datetime.fromisoformat(end_date_str))

        logger.info(f"Syncing games for {season_year} from {start_date_str} to {end_date_str}")

        espn_client = get_espn_client()
        espn_games = espn_client.fetch_games_in_range(start_date, end_date)

        created_count = 0
        updated_count = 0

        for external_id, event in espn_games.items():
            parsed_data = espn_client.parse_game_data(event)
            if not parsed_data or not parsed_data.get('kickoff'):
                continue

            # Find or create teams
            home_team = GameUpdateService.find_or_create_team(
                season,
                parsed_data['home_team_espn_id'],
                parsed_data['home_team_name'],
                parsed_data['home_team_abbr']
            )
            away_team = GameUpdateService.find_or_create_team(
                season,
                parsed_data['away_team_espn_id'],
                parsed_data['away_team_name'],
                parsed_data['away_team_abbr']
            )

            if not home_team or not away_team:
                logger.warning(f"Could not create teams for game {external_id}")
                continue

            # Find or create game
            game, created = Game.objects.get_or_create(
                external_id=external_id,
                defaults={
                    'season': season,
                    'home_team': home_team,
                    'away_team': away_team,
                    'kickoff': parsed_data['kickoff'],
                    'home_score': parsed_data.get('home_score'),
                    'away_score': parsed_data.get('away_score'),
                    'quarter': parsed_data.get('period'),
                    'clock': parsed_data.get('clock', ''),
                    'is_final': parsed_data.get('is_final', False),
                }
            )

            if created:
                created_count += 1
                logger.info(f"Created game: {game}")
            else:
                # Update existing game
                if GameUpdateService.update_game_from_espn_data(game, parsed_data):
                    updated_count += 1

        logger.info(
            f"Sync complete: {created_count} games created, {updated_count} games updated"
        )

    except Season.DoesNotExist:
        logger.error(f"Season {season_year} not found")
    except Exception as e:
        logger.error(f"Error syncing games: {e}", exc_info=True)


@shared_task(name='cfb.tasks.sync_upcoming_games')
def sync_upcoming_games():
    """
    Periodic task to sync upcoming games from CFBD (with ESPN fallback).
    Runs daily to ensure new games are added to the database.
    Uses CFBD API if configured, otherwise falls back to ESPN.
    """
    try:
        from .services.schedule import fetch_and_store_week
        
        logger.info("Starting upcoming games sync")
        
        # Sync current week's games
        # This uses CFBD if API key is configured, otherwise ESPN
        count = fetch_and_store_week()
        
        logger.info(f"Upcoming games sync completed: {count} games synced")

    except Exception as e:
        logger.error(f"Error syncing upcoming games: {e}", exc_info=True)


@shared_task(
    bind=True,
    name='cfb.tasks.update_spreads',
    max_retries=3,
    default_retry_delay=300,
)
def update_spreads(self):
    """
    Periodic task to update game spreads/odds from The Odds API.
    Fetches spreads for all games in the current week.
    
    NOTE: Spreads captured on game day (9 AM daily) are considered the final
    spreads for that game. No post-game spread updates are performed.
    This ensures consistent 7 API calls per week maximum.
    """
    try:
        from .services.odds import update_odds_for_week_games

        logger.info("Starting spread update task")
        
        # Check if we have an API key configured
        if not settings.ODDS_API_KEY:
            logger.warning("ODDS_API_KEY not configured, skipping spread update")
            return

        # Update spreads for the current week's games
        updated_count = update_odds_for_week_games()
        
        logger.info(f"Spread update complete: {updated_count} games updated")

        # Store last successful spread poll time
        cache.set('spreads:last_poll', timezone.now().isoformat(), timeout=86400)  # 24 hours

    except Exception as exc:
        logger.error(f"Error in spread update task: {exc}", exc_info=True)
        # Retry with delay
        raise self.retry(exc=exc)

