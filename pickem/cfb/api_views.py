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

from .models import DEFAULT_SPORT, Game, Season, Sport, GameSpread, LeagueGame
from .services.sports import get_active_season, game_cache_key

logger = logging.getLogger(__name__)


def _serialize_team(team, sport_id) -> Dict[str, Any]:
    """Serialize a Team instance to dictionary."""
    return {
        'id': team.id,
        'name': team.name,
        'abbreviation': team.abbreviation or team.name[:4].upper(),
        'nickname': team.nickname,
        'sport': sport_id,
        'provider_ids': team.provider_ids,
        'logo_url': team.logo_url,
        'conference': team.conference,
        'primary_color': team.primary_color,
        'record': f"{team.record_wins}-{team.record_losses}",
        'ats_record': (
            f"{team.ats_wins}-{team.ats_losses}-{team.ats_pushes}"
            if team.ats_pushes
            else f"{team.ats_wins}-{team.ats_losses}"
        ),
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
        'sport': game.season.sport_id,
        'season_id': game.season_id,
        'provider_ids': game.provider_ids,
        'tournament': game.tournament,
        'home_seed': game.home_seed,
        'away_seed': game.away_seed,
        'schedule_period': {'id': game.schedule_period_id, 'name': game.schedule_period.display_name, 'kind': game.schedule_period.kind} if game.schedule_period_id else None,
        'start_time': game.start_time.isoformat(),
        'period': game.period,
        'period_display': game.period_display,
        'home_team': _serialize_team(game.home_team, game.season.sport_id),
        'away_team': _serialize_team(game.away_team, game.season.sport_id),
        'kickoff': game.start_time.isoformat(),
        'home_score': game.home_score,
        'away_score': game.away_score,
        'quarter': game.period,
        'clock': game.clock,
        'has_possession': game.season.sport.has_possession,
        'has_downs': game.season.sport.has_downs,
        'possession': game.possession or '',
        'down_distance_text': game.down_distance_text or '',
        'ball_on': game.ball_on or '',
        'is_final': game.is_final,
        'spread': {
            'home': float(game.current_home_spread) if game.current_home_spread is not None else None,
            'away': float(game.current_away_spread) if game.current_away_spread is not None else None,
        },
    }

    # Include cached ESPN data if requested
    if include_cached_data and (game.external_id or game.provider_ids.get("espn")):
        cache_key = game_cache_key(game)
        cached_data = cache.get(cache_key)
        if cached_data:
            data['status_state'] = cached_data.get('status_state')
            data['status_detail'] = cached_data.get('status_detail')
            data['broadcast_network'] = cached_data.get('broadcast_network')

    return data


@require_GET
@cache_page(5)  # Short cache so live clocks aren't stuck behind CDN/page cache
def games_list(request):
    """
    Public API endpoint to list games with optional filtering.
    
    Query parameters:
        - date: Filter by date (YYYY-MM-DD format)
        - live: Filter for live games only (true/false)
        - sport: Sport slug (defaults to college-football)
        - season: Filter by season year within that sport
        - team: Filter by team ID
        - ids: Comma-separated game IDs to return
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
        game_ids = request.GET.get('ids')
        limit = min(int(request.GET.get('limit', 100)), 500)

        # Start with base queryset
        games = Game.objects.select_related(
            'home_team',
            'away_team',
            'season__sport',
            'schedule_period'
        ).order_by('start_time')

        # Filter by season
        if season_year:
            try:
                season = Season.objects.get(sport_id=request.GET.get("sport", DEFAULT_SPORT), year=int(season_year))
                games = games.filter(season=season)
            except (Season.DoesNotExist, ValueError):
                return JsonResponse({
                    'error': f'Season {season_year} not found'
                }, status=404)
        else:
            # Default to active season
            active_season = get_active_season(request.GET.get("sport", DEFAULT_SPORT))
            if game_ids and "sport" not in request.GET:
                games = games.filter(season__is_active=True)
            elif active_season:
                games = games.filter(season=active_season)
            else:
                games = games.none()

        # Let polling clients request exactly the games rendered on the page.
        # This must happen before slicing so late-season games are not hidden by
        # the endpoint's start_time ordering and result limit.
        if game_ids:
            try:
                requested_ids = {
                    int(game_id) for game_id in game_ids.split(',') if game_id
                }
                if not requested_ids or len(requested_ids) > 500:
                    raise ValueError
                games = games.filter(id__in=requested_ids)
            except ValueError:
                return JsonResponse({
                    'error': 'Invalid game IDs. Provide up to 500 comma-separated integers'
                }, status=400)

        # Filter by date
        if date_str:
            try:
                target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                start_of_day = timezone.make_aware(
                    datetime.combine(target_date, datetime.min.time())
                )
                end_of_day = start_of_day + timedelta(days=1)
                games = games.filter(start_time__gte=start_of_day, start_time__lt=end_of_day)
            except ValueError:
                return JsonResponse({
                    'error': 'Invalid date format. Use YYYY-MM-DD'
                }, status=400)

        # Filter by live status
        if live_only:
            # Live games are those that have started but not finished
            now = timezone.now()
            games = games.filter(
                start_time__lte=now,
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
            'season__sport',
            'schedule_period'
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
        active_season = get_active_season(request.GET.get("sport", DEFAULT_SPORT))
        if not active_season:
            return JsonResponse({
                'games': [],
                'count': 0,
                'message': 'No active season'
            })

        # Find live games
        live_games_qs = Game.objects.select_related(
            'home_team',
            'away_team',
            'season__sport',
            'schedule_period'
        ).filter(
            season=active_season,
            start_time__lte=now,
            is_final=False
        ).exclude(
            home_score__isnull=True,
            away_score__isnull=True
        ).order_by('start_time')

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
        active_season = get_active_season(request.GET.get("sport", DEFAULT_SPORT))
        if not active_season:
            return JsonResponse({
                'games': [],
                'count': 0,
                'message': 'No active season'
            })

        # Find upcoming games
        upcoming_games_qs = Game.objects.select_related(
            'home_team',
            'away_team',
            'season__sport',
            'schedule_period'
        ).filter(
            season=active_season,
            start_time__gte=now,
            start_time__lte=end_date
        ).order_by('start_time')[:100]

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
        active_season = get_active_season(request.GET.get("sport", DEFAULT_SPORT))
        season_info = None
        if active_season:
            season_info = {
                'sport': active_season.sport_id,
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


@require_GET
@cache_page(60)  # Cache for 1 minute
def game_spread_history(request, game_id):
    """
    Get historical spread data for a specific game.
    
    Args:
        game_id: Database ID of the game
    
    Query parameters:
        - league_id: League ID to get locked spread info (optional)
    
    Example:
        /api/games/123/spread-history?league_id=456
    """
    try:
        # Get the game
        game = Game.objects.select_related('home_team', 'away_team').get(id=game_id)
        
        # Get locked spread info if league_id is provided
        locked_spread = None
        league_id = request.GET.get('league_id')
        if league_id:
            try:
                league_game = LeagueGame.objects.get(
                    league_id=league_id,
                    game=game,
                    is_active=True
                )
                if league_game.locked_home_spread is not None:
                    locked_spread = {
                        'home_spread': float(league_game.locked_home_spread),
                        'away_spread': float(league_game.locked_away_spread),
                        'locked_at': league_game.spread_locked_at.isoformat() if league_game.spread_locked_at else None
                    }
            except LeagueGame.DoesNotExist:
                pass
        
        # Get historical spreads from GameSpread model
        spreads = GameSpread.objects.filter(game=game).order_by('timestamp')
        
        # Debug logging
        logger.debug(
            "Game %s: %s GameSpread records; current=%s opening=%s",
            game_id,
            spreads.count(),
            game.current_home_spread,
            game.opening_home_spread,
        )
        
        # Serialize spread data
        spread_data = []
        for spread in spreads:
            spread_data.append({
                'timestamp': spread.timestamp.isoformat(),
                'home_spread': float(spread.home_spread),
                'away_spread': float(spread.away_spread),
                'source': spread.source
            })
        
        # If no historical data exists but we have current/opening spreads, create a simple chart
        if not spread_data and (game.current_home_spread is not None or game.opening_home_spread is not None):
            from datetime import datetime, timedelta
            from django.utils import timezone
            
            # Create a simple 2-point chart: opening spread and current spread
            now = timezone.now()
            opening_spread = game.opening_home_spread
            current_spread = game.current_home_spread
            
            if opening_spread is not None:
                # Opening spread point (3 days ago)
                opening_time = now - timedelta(days=3)
                spread_data.append({
                    'timestamp': opening_time.isoformat(),
                    'home_spread': float(opening_spread),
                    'away_spread': float(-opening_spread),
                    'source': 'Opening Spread'
                })
            
            if current_spread is not None:
                # Current spread point (now)
                spread_data.append({
                    'timestamp': now.isoformat(),
                    'home_spread': float(current_spread),
                    'away_spread': float(-current_spread),
                    'source': 'Current Spread'
                })
            
            # If we only have one data point, add some intermediate points for better visualization
            if len(spread_data) == 1:
                base_spread = spread_data[0]['home_spread']
                for i in range(1, 4):  # Add 3 intermediate points
                    days_ago = 2 - i
                    timestamp = now - timedelta(days=days_ago)
                    # Add small random variation
                    import random
                    variation = random.uniform(-0.5, 0.5)
                    sample_spread = base_spread + variation
                    
                    spread_data.append({
                        'timestamp': timestamp.isoformat(),
                        'home_spread': sample_spread,
                        'away_spread': -sample_spread,
                        'source': 'Estimated'
                    })
            
            # Sort by timestamp
            spread_data.sort(key=lambda x: x['timestamp'])
        
        # Get game info
        game_info = {
            'id': game.id,
            'home_team': {
                'id': game.home_team.id,
                'name': game.home_team.name,
                'abbreviation': game.home_team.abbreviation or game.home_team.name[:4].upper()
            },
            'away_team': {
                'id': game.away_team.id,
                'name': game.away_team.name,
                'abbreviation': game.away_team.abbreviation or game.away_team.name[:4].upper()
            },
            'start_time': game.start_time.isoformat(),
            'current_spread': {
                'home': float(game.current_home_spread) if game.current_home_spread is not None else None,
                'away': float(game.current_away_spread) if game.current_away_spread is not None else None
            },
            'opening_spread': {
                'home': float(game.opening_home_spread) if game.opening_home_spread else None,
                'away': float(game.opening_away_spread) if game.opening_away_spread else None
            }
        }
        
        response_data = {
            'game': game_info,
            'spread_history': spread_data,
            'locked_spread': locked_spread,
            'count': len(spread_data),
            'debug': {
                'game_id': game_id,
                'has_current_spread': game.current_home_spread is not None,
                'has_opening_spread': game.opening_home_spread is not None,
                'game_spread_count': spreads.count(),
                'league_id_provided': league_id is not None,
                'has_locked_spread': locked_spread is not None,
                'locked_spread_details': locked_spread,
                'spread_data_sample': spread_data[:3] if spread_data else []
            }
        }
        
        return JsonResponse(response_data)
        
    except Game.DoesNotExist:
        return JsonResponse({
            'error': 'Game not found'
        }, status=404)
    except Exception as e:
        logger.error(f"Error in game_spread_history API: {e}", exc_info=True)
        return JsonResponse({
            'error': 'Internal server error'
        }, status=500)


@require_GET
@cache_page(60)
def sports_list(request):
    """Discover configured sports and their independent active seasons."""
    active_seasons = {
        season.sport_id: season
        for season in Season.objects.filter(is_active=True).order_by("year")
    }
    sports = []
    for sport in Sport.objects.filter(is_active=True):
        season = active_seasons.get(sport.pk)
        sports.append({
            "slug": sport.pk,
            "name": sport.name,
            "period_label": sport.period_label,
            "regulation_periods": sport.regulation_periods,
            "has_possession": sport.has_possession,
            "has_downs": sport.has_downs,
            "active_season": {"id": season.pk, "year": season.year, "name": season.name} if season else None,
        })
    return JsonResponse({"sports": sports})
