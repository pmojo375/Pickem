"""
Public API views for game data.
These endpoints are designed for frontend polling and don't require authentication.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

from .models import Game, Season

logger = logging.getLogger(__name__)


def _serialize_team(team) -> Dict[str, Any]:
    """Serialize a Team instance to dictionary."""
    return {
        'id': team.id,
        'name': team.name,
        'abbreviation': team.abbreviation or team.name[:4].upper(),
        'nickname': team.nickname,
        'logo_url': team.logo_url,
        'conference': team.conference,
        'primary_color': team.primary_color,
        'record': f"{team.record_wins}-{team.record_losses}",
    }


def _serialize_game(game: Game, include_cached_data: bool = True) -> Dict[str, Any]:
    """
    Serialize a Game instance to dictionary.
    
    Args:
        game: Game instance to serialize
        include_cached_data: Whether to include additional cached ESPN data
    """
    data = {
        'id': game.id,
        'external_id': game.external_id,
        'home_team': _serialize_team(game.home_team),
        'away_team': _serialize_team(game.away_team),
        'kickoff': game.kickoff.isoformat(),
        'home_score': game.home_score,
        'away_score': game.away_score,
        'quarter': game.quarter,
        'clock': game.clock,
        'is_final': game.is_final,
        'spread': {
            'home': float(game.current_home_spread) if game.current_home_spread else None,
            'away': float(game.current_away_spread) if game.current_away_spread else None,
        },
    }

    # Include cached ESPN data if requested
    if include_cached_data and game.external_id:
        cache_key = f"{settings.REDIS_KEY_GAME_PREFIX}{game.external_id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            data['status_state'] = cached_data.get('status_state')
            data['status_detail'] = cached_data.get('status_detail')
            data['broadcast_network'] = cached_data.get('broadcast_network')

    return data


@require_GET
@cache_page(30)  # Cache for 30 seconds
def games_list(request):
    """
    Public API endpoint to list games with optional filtering.
    
    Query parameters:
        - date: Filter by date (YYYY-MM-DD format)
        - live: Filter for live games only (true/false)
        - season: Filter by season year (defaults to active season)
        - team: Filter by team ID
        - limit: Maximum number of results (default: 100, max: 500)
    
    Example:
        /api/games?date=2024-09-28&live=true
        /api/games?season=2024&team=123
    """
    try:
        # Get query parameters
        date_str = request.GET.get('date')
        live_only = request.GET.get('live', '').lower() == 'true'
        season_year = request.GET.get('season')
        team_id = request.GET.get('team')
        limit = min(int(request.GET.get('limit', 100)), 500)

        # Start with base queryset
        games = Game.objects.select_related(
            'home_team',
            'away_team',
            'season'
        ).order_by('kickoff')

        # Filter by season
        if season_year:
            try:
                season = Season.objects.get(year=int(season_year))
                games = games.filter(season=season)
            except (Season.DoesNotExist, ValueError):
                return JsonResponse({
                    'error': f'Season {season_year} not found'
                }, status=404)
        else:
            # Default to active season
            active_season = Season.objects.filter(is_active=True).first()
            if active_season:
                games = games.filter(season=active_season)

        # Filter by date
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                start_of_day = timezone.make_aware(
                    datetime.combine(target_date, datetime.min.time())
                )
                end_of_day = start_of_day + timedelta(days=1)
                games = games.filter(kickoff__gte=start_of_day, kickoff__lt=end_of_day)
            except ValueError:
                return JsonResponse({
                    'error': 'Invalid date format. Use YYYY-MM-DD'
                }, status=400)

        # Filter by live status
        if live_only:
            # Live games are those that have started but not finished
            now = timezone.now()
            games = games.filter(
                kickoff__lte=now,
                is_final=False
            ).exclude(
                home_score__isnull=True,
                away_score__isnull=True
            )

        # Filter by team
        if team_id:
            try:
                from django.db.models import Q
                team_id_int = int(team_id)
                games = games.filter(
                    Q(home_team_id=team_id_int) | Q(away_team_id=team_id_int)
                )
            except ValueError:
                return JsonResponse({
                    'error': 'Invalid team ID'
                }, status=400)

        # Apply limit
        games = games[:limit]

        # Serialize results
        game_list = [_serialize_game(game) for game in games]

        # Get live state from cache for metadata
        live_state = cache.get(settings.REDIS_KEY_LIVE_STATE) or {}

        return JsonResponse({
            'games': game_list,
            'count': len(game_list),
            'metadata': {
                'has_live_games': live_state.get('has_live_games', False),
                'live_game_count': live_state.get('live_game_count', 0),
                'last_update': live_state.get('last_check'),
            }
        })

    except Exception as e:
        logger.error(f"Error in games_list API: {e}", exc_info=True)
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


@require_GET
@cache_page(60)  # Cache for 1 minute
def game_detail(request, game_id):
    """
    Get detailed information about a specific game.
    
    Args:
        game_id: Database ID of the game
    
    Example:
        /api/games/123
    """
    try:
        game = Game.objects.select_related(
            'home_team',
            'away_team',
            'season'
        ).get(id=game_id)

        game_data = _serialize_game(game, include_cached_data=True)

        # Add additional details
        game_data['season'] = {
            'year': game.season.year,
            'name': game.season.name,
        }

        return JsonResponse({
            'game': game_data
        })

    except Game.DoesNotExist:
        return JsonResponse({
            'error': 'Game not found'
        }, status=404)
    except Exception as e:
        logger.error(f"Error in game_detail API: {e}", exc_info=True)
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


@require_GET
def live_games(request):
    """
    Get all currently live games.
    This endpoint is not cached to ensure real-time data.
    
    Example:
        /api/games/live
    """
    try:
        now = timezone.now()
        
        # Get active season
        active_season = Season.objects.filter(is_active=True).first()
        if not active_season:
            return JsonResponse({
                'games': [],
                'count': 0,
                'message': 'No active season'
            })

        # Find live games
        live_games_qs = Game.objects.select_related(
            'home_team',
            'away_team'
        ).filter(
            season=active_season,
            kickoff__lte=now,
            is_final=False
        ).exclude(
            home_score__isnull=True,
            away_score__isnull=True
        ).order_by('kickoff')

        game_list = [_serialize_game(game) for game in live_games_qs]

        return JsonResponse({
            'games': game_list,
            'count': len(game_list),
            'timestamp': timezone.now().isoformat(),
        })

    except Exception as e:
        logger.error(f"Error in live_games API: {e}", exc_info=True)
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


@require_GET
@cache_page(60)  # Cache for 1 minute
def upcoming_games(request):
    """
    Get upcoming games (not yet started).
    
    Query parameters:
        - days: Number of days to look ahead (default: 7, max: 30)
    
    Example:
        /api/games/upcoming?days=3
    """
    try:
        days = min(int(request.GET.get('days', 7)), 30)
        now = timezone.now()
        end_date = now + timedelta(days=days)

        # Get active season
        active_season = Season.objects.filter(is_active=True).first()
        if not active_season:
            return JsonResponse({
                'games': [],
                'count': 0,
                'message': 'No active season'
            })

        # Find upcoming games
        upcoming_games_qs = Game.objects.select_related(
            'home_team',
            'away_team'
        ).filter(
            season=active_season,
            kickoff__gte=now,
            kickoff__lte=end_date
        ).order_by('kickoff')[:100]

        game_list = [_serialize_game(game) for game in upcoming_games_qs]

        return JsonResponse({
            'games': game_list,
            'count': len(game_list),
            'date_range': {
                'start': now.isoformat(),
                'end': end_date.isoformat(),
            }
        })

    except Exception as e:
        logger.error(f"Error in upcoming_games API: {e}", exc_info=True)
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


@require_GET
@cache_page(120)  # Cache for 2 minutes
def system_status(request):
    """
    Get system status including circuit breaker state and polling info.
    Useful for monitoring and debugging.
    
    Example:
        /api/system/status
    """
    try:
        # Get live state
        live_state = cache.get(settings.REDIS_KEY_LIVE_STATE) or {}

        # Get last poll time
        last_poll_timestamp = cache.get(settings.REDIS_KEY_LAST_POLL)
        last_poll = None
        if last_poll_timestamp:
            last_poll = datetime.fromtimestamp(last_poll_timestamp).isoformat()

        # Get active season info
        active_season = Season.objects.filter(is_active=True).first()
        season_info = None
        if active_season:
            season_info = {
                'year': active_season.year,
                'name': active_season.name,
                'total_games': Game.objects.filter(season=active_season).count(),
            }

        return JsonResponse({
            'status': 'ok',
            'timestamp': timezone.now().isoformat(),
            'live_state': live_state,
            'last_poll': last_poll,
            'active_season': season_info,
            'polling_optimized': True
        })

    except Exception as e:
        logger.error(f"Error in system_status API: {e}", exc_info=True)
        return JsonResponse({
            'status': 'error',
            'error': str(e)
        }, status=500)

