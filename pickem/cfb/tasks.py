"""
Celery tasks for ESPN game score polling and updates.
Implements intelligent polling with Redis caching and dynamic intervals.
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import timedelta, datetime
from decimal import Decimal

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .models import Game, Team, Season, Location, Week, Ranking, GameSpread
from .services.cfbd_api import get_cfbd_client
from .services.live import grade_picks_for_game, fetch_and_store_live_scores

logger = logging.getLogger(__name__)


@shared_task(bind=True, name='cfb.tasks.poll_espn_scores', max_retries=3, default_retry_delay=60,)
def poll_espn_scores(self):
    """
    Main task to poll ESPN for live game scores and update database.
    Only polls ESPN when there are active games (started but not final).
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

        logger.info("Starting ESPN live score polling")
        
        # Check if there are active games
        now = timezone.now()
        active_season = Season.objects.filter(is_active=True).first()
        if not active_season:
            logger.warning("No active season found")
            return

        start_date = now - timedelta(days=settings.GAME_CHECK_WINDOW_PAST)
        end_date = now + timedelta(days=settings.GAME_CHECK_WINDOW_FUTURE)

        active_games = Game.objects.filter(
            season=active_season,
            kickoff__gte=start_date,
            kickoff__lte=end_date,
            is_final=False,
            kickoff__lte=now
        )

        if not active_games.exists():
            logger.debug("No active games need polling")
            return

        logger.info(f"Found {active_games.count()} active games")

        # Fetch and store live scores
        updated_count = fetch_and_store_live_scores()
        
        logger.info(f"ESPN polling complete: {updated_count} games updated")

    except Exception as exc:
        logger.error(f"Error in ESPN polling task: {exc}", exc_info=True)
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


@shared_task(name='cfb.tasks.pull_calendar')
def pull_calendar(season_year: int, force: bool = False):
    """
    Pull the calendar for a given season.
    """
    try:
        cfbd_client = get_cfbd_client()
        calendar_data = cfbd_client.fetch_calendar(season_year)
        if not calendar_data:
            logger.error(f"No calendar data returned from CFBD for {season_year}")
            return
        logger.info(f"Processing {len(calendar_data)} calendar data")
        
        season = Season.objects.get(year=season_year)
        
        for calendar_item in calendar_data:
            logger.info(f"Processing calendar item: {calendar_item}")
            
            start_date = calendar_item['startDate']
            end_date = calendar_item['endDate']
        
            try:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                
                # Ensure timezone-aware
                if timezone.is_naive(start_date):
                    start_date = timezone.make_aware(start_date)
                if timezone.is_naive(end_date):
                    end_date = timezone.make_aware(end_date)
            except (ValueError, AttributeError) as e:
                logger.warning(f"Invalid start_date for game {calendar_item['week']}: {e}")
                continue
            
            # Update or create week
            Week.objects.update_or_create(
                season=season,
                season_type=calendar_item['seasonType'],
                number=calendar_item['week'],
                start_date=start_date,
                end_date=end_date
            )
            
    except Exception as e:
        logger.error(f"Error pulling calendar: {e}", exc_info=True)


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


@shared_task(bind=True, name='cfb.tasks.update_spreads', max_retries=3, default_retry_delay=300,)
def update_spreads(self, season_year: int, season_type: str = 'regular', week: int = None):
    """
    Periodic task to update game spreads/odds from CFBD.
    Fetches spreads for all games in the current week.
    
    NOTE: Spreads captured on game day (9 AM daily) are considered the final
    spreads for that game. No post-game spread updates are performed.
    This ensures consistent 7 API calls per week maximum.
    
    Args:
        season_year: Year of the season
        season_type: 'regular' or 'postseason'
        week: Week number to update spreads for
    """
    try:
        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            logger.error(f"Season {season_year} not found")
            return
        
        logger.info(f"Updating spreads for {season_year} {season_type} week {week}")
        
        # Fetch lines from CFBD
        cfbd_client = get_cfbd_client()
        lines_data = cfbd_client.fetch_lines(
            year=season_year,
            week=week,
            season_type=season_type
        )
        
        if not lines_data:
            logger.error(f"No lines data returned from CFBD for {season_year} {season_type} week {week}")
            return

        logger.info(f"Fetched {len(lines_data)} games with lines from CFBD")

        games_updated = 0
        games_not_found = 0
        games_no_lines = 0

        for game_data in lines_data:
            home_team_name = game_data.get('homeTeam')
            away_team_name = game_data.get('awayTeam')
            lines = game_data.get('lines', [])

            if not lines:
                logger.warning(f"No lines for {away_team_name} @ {home_team_name}")
                games_no_lines += 1
                continue

            # Find the game in our database
            game = Game.objects.filter(
                season=season,
                week=week,
                season_type=season_type,
                home_team__name__iexact=home_team_name,
                away_team__name__iexact=away_team_name
            ).first()

            if not game:
                logger.warning(f"Game not found in DB: {away_team_name} @ {home_team_name}")
                games_not_found += 1
                continue

            # Extract first line with a valid spread
            spread_data = None
            for line in lines:
                spread = line.get('spread')
                spread_open = line.get('spreadOpen')
                
                if spread is not None:
                    # Spread is from home team's perspective
                    home_spread = Decimal(str(spread))
                    away_spread = -home_spread
                    
                    # Handle opening spread
                    if spread_open is not None:
                        home_spread_open = Decimal(str(spread_open))
                        away_spread_open = -home_spread_open
                    else:
                        # If no opening spread, use current spread
                        home_spread_open = home_spread
                        away_spread_open = away_spread
                    
                    spread_data = {
                        'home_spread': home_spread,
                        'away_spread': away_spread,
                        'home_spread_open': home_spread_open,
                        'away_spread_open': away_spread_open,
                        'provider': line.get('provider', 'Unknown'),
                    }
                    break
                        
            if not spread_data:
                logger.warning(f"No valid spread found for {away_team_name} @ {home_team_name}")
                games_no_lines += 1
                continue

            # Update the game and create spread record
            try:
                home_spread = spread_data['home_spread']
                away_spread = spread_data['away_spread']
                home_spread_open = spread_data['home_spread_open']
                away_spread_open = spread_data['away_spread_open']
                provider = spread_data['provider']
                
                # Create GameSpread record to track this spread update
                GameSpread.objects.create(
                    game=game,
                    home_spread=home_spread,
                    away_spread=away_spread,
                    source=provider
                )
                
                # Always update current spread
                game.current_home_spread = home_spread
                game.current_away_spread = away_spread
                
                # Set opening spread only if not already set
                if game.opening_home_spread is None:
                    game.opening_home_spread = home_spread_open
                    game.opening_away_spread = away_spread_open
                    game.save(update_fields=[
                        'current_home_spread',
                        'current_away_spread',
                        'opening_home_spread',
                        'opening_away_spread'
                    ])
                else:
                    game.save(update_fields=[
                        'current_home_spread',
                        'current_away_spread'
                    ])
                
                games_updated += 1
                logger.debug(f"Updated spreads for {away_team_name} @ {home_team_name}: {provider} {home_spread}/{away_spread}")
                
            except Exception as e:
                logger.error(f"Error updating spreads for {away_team_name} @ {home_team_name}: {e}", exc_info=True)
                continue

        logger.info(
            f"Spread update complete for {season_year} week {week}: "
            f"{games_updated} updated, {games_not_found} not found, {games_no_lines} no lines"
        )
        
    except Exception as exc:
        logger.error(f"Error in update_spreads task: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, name='cfb.tasks.update_rankings', max_retries=3, default_retry_delay=300,)
def update_rankings(self, season_year: int, season_type: str = 'regular', week: int = None):
    """
    Update rankings for a given season and week.
    
    Args:
        season_year: Year of the season
        season_type: 'regular' or 'postseason'
        week: Specific week number (optional, fetches all weeks if not provided)
    """
    try:
        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            logger.error(f"Season {season_year} not found")
            return
        
        logger.info(f"Updating rankings for {season_year} {season_type}" + (f" week {week}" if week else ""))
        
        # Fetch rankings from CFBD
        cfbd_client = get_cfbd_client()
        rankings_data = cfbd_client.fetch_rankings(
            year=season_year,
            week=week,
            season_type=season_type
        )
        
        if not rankings_data:
            logger.error(f"No rankings data returned from CFBD for {season_year} {season_type}" + (f" week {week}" if week else ""))
            return
        
        logger.info(f"Fetched {len(rankings_data)} weeks of rankings from CFBD")
        
        total_created = 0
        total_updated = 0
        total_skipped = 0
        
        for week_data in rankings_data:
            week_number = week_data.get('week')
            polls = week_data.get('polls', [])

            if not polls:
                logger.warning(f"No polls for Week {week_number}")
                continue

            for poll_data in polls:
                poll_name = poll_data.get('poll')
                ranks = poll_data.get('ranks', [])

                logger.info(f"Week {week_number} - {poll_name}: {len(ranks)} teams")

                for rank_data in ranks:
                    school_name = rank_data.get('school')
                    team_id = rank_data.get('teamId')
                    rank = rank_data.get('rank')
                    first_place_votes = rank_data.get('firstPlaceVotes', 0)
                    points = rank_data.get('points', 0)
                    
                    # Find team by CFBD ID or name
                    team = None
                    if team_id:
                        team = Team.objects.filter(
                            season=season,
                            cfbd_id=team_id
                        ).first()
                    
                    if not team:
                        team = Team.objects.filter(
                            season=season,
                            name__iexact=school_name
                        ).first()
                    
                    if not team:
                        logger.warning(f"Team not found in DB: {school_name} (ID: {team_id})")
                        continue

                    # Get the Week object
                    try:
                        week_obj = Week.objects.get(
                            season=season,
                            number=week_number,
                            season_type=season_type
                        )
                    except Week.DoesNotExist:
                        logger.error(f"Week {week_number} not found for {season_year} {season_type}")
                        continue
                    
                    # Check if ranking already exists
                    try:
                        existing_ranking = Ranking.objects.get(
                            season=season,
                            week=week_obj,
                            season_type=season_type,
                            team=team,
                            poll=poll_name
                        )
                        
                        # Check if anything changed
                        if (existing_ranking.rank == rank and
                            existing_ranking.first_place_votes == first_place_votes and
                            existing_ranking.points == points):
                            total_skipped += 1
                            continue
                        
                        # Update the ranking
                        existing_ranking.rank = rank
                        existing_ranking.first_place_votes = first_place_votes
                        existing_ranking.points = points
                        existing_ranking.save()
                        total_updated += 1
                        
                    except Ranking.DoesNotExist:
                        # Create new ranking
                        Ranking.objects.create(
                            season=season,
                            week=week_obj,
                            season_type=season_type,
                            team=team,
                            poll=poll_name,
                            rank=rank,
                            first_place_votes=first_place_votes,
                            points=points
                        )
                        total_created += 1
        
        logger.info(
            f"Rankings update complete for {season_year}: "
            f"{total_created} created, {total_updated} updated, {total_skipped} skipped"
        )
        
    except Exception as exc:
        logger.error(f"Error in update_rankings task: {exc}", exc_info=True)
        raise self.retry(exc=exc)


# ============================================================================
# CFBD Season Initialization Tasks
# ============================================================================


@shared_task(name='cfb.tasks.pull_season_teams')
def pull_season_teams(season_year: int, force: bool = False):
    """
    One-time task to pull all FBS teams for a season from CFBD API.
    Sets the teams_pulled flag on completion.
    
    Args:
        season_year: Year of the season
        force: If True, pull even if already pulled
    """
    try:
        season = Season.objects.get(year=season_year)
        
        # Check if already pulled
        if season.teams_pulled and not force:
            logger.info(f"Teams already pulled for {season_year}, skipping")
            return
        
        logger.info(f"Pulling teams data for {season_year} season")
        
        # Get CFBD client
        cfbd_client = get_cfbd_client()
        
        # Fetch teams
        teams_data = cfbd_client.fetch_teams(season_year)
        
        if not teams_data:
            logger.error(f"No teams data returned from CFBD for {season_year}")
            return
        
        logger.info(f"Processing {len(teams_data)} teams")
        
        created_count = 0
        updated_count = 0
        
        for team_data in teams_data:
            try:
                # Extract team fields (CFBD uses camelCase)
                cfbd_id = team_data.get('id')
                school = team_data.get('school', '')
                
                if not school:
                    logger.warning(f"Team missing school name, skipping: {team_data}")
                    continue
                
                mascot = team_data.get('mascot', '')
                abbreviation = team_data.get('abbreviation', '')
                conference = team_data.get('conference', '')
                division = team_data.get('division') or ''  # Handle None from API
                classification = team_data.get('classification', '')
                
                # Only store FBS and FCS teams (skip Division I, II, III, etc.)
                if classification not in ('fbs', 'fcs'):
                    logger.debug(f"Skipping non-FBS/FCS team: {school} ({classification})")
                    continue
                
                color = team_data.get('color', '')
                alt_color = team_data.get('alternateColor', '')  # camelCase!
                twitter = team_data.get('twitter', '')
                
                # Normalize colors
                if color and not color.startswith('#'):
                    color = f'#{color}'
                if alt_color and not alt_color.startswith('#'):
                    alt_color = f'#{alt_color}'
                
                # Get logos
                logos = team_data.get('logos', [])
                logo_url = logos[0] if logos else ''
                
                # Handle location data (CFBD uses camelCase)
                location_obj = None
                location_data = team_data.get('location')
                
                # Only create Location if we have meaningful data
                if location_data and isinstance(location_data, dict) and location_data.get('name'):
                    try:
                        location_obj = Location.objects.create(
                            name=location_data.get('name') or None,
                            city=location_data.get('city') or None,
                            state=location_data.get('state') or None,
                            zip=location_data.get('zip') or None,
                            country_code=location_data.get('countryCode') or None,
                            timezone=location_data.get('timezone') or None,
                            latitude=location_data.get('latitude'),
                            longitude=location_data.get('longitude'),
                            elevation=location_data.get('elevation'),
                            capacity=location_data.get('capacity'),
                            year_constructed=location_data.get('constructionYear'),
                            grass=location_data.get('grass'),
                            dome=location_data.get('dome'),
                        )
                    except Exception as e:
                        logger.warning(f"Could not create location for {school}: {e}")
                        location_obj = None
                
                # Create or update team (use season+name as unique key, matching model constraint)
                team, created = Team.objects.update_or_create(
                    season=season,
                    name=school,  # Use name as lookup key (matches unique_together constraint)
                    defaults={
                        'cfbd_id': cfbd_id,  # Update cfbd_id in defaults
                        'nickname': mascot,
                        'abbreviation': abbreviation,
                        'conference': conference,
                        'division': division,
                        'classification': classification,
                        'logo_url': logo_url,
                        'primary_color': color,
                        'alt_color': alt_color,
                        'twitter': twitter,
                        'location': location_obj,
                    }
                )
                
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            
            except Exception as e:
                logger.error(f"❌ ERROR processing team '{school}': {type(e).__name__}: {e}")
                logger.error(f"Team data: {team_data}")
                # Don't continue - let it fail so we can see the error
                raise
        
        # Mark as pulled
        season.teams_pulled = True
        season.save(update_fields=['teams_pulled'])
        
        logger.info(
            f"Teams pull complete for {season_year}: "
            f"{created_count} created, {updated_count} updated"
        )
    
    except Season.DoesNotExist:
        logger.error(f"Season {season_year} not found")
    except Exception as e:
        logger.error(f"Error pulling teams for {season_year}: {e}", exc_info=True)


@shared_task(name='cfb.tasks.pull_season_games')
def pull_season_games(season_year: int, season_type: str = 'regular', force: bool = False):
    """
    One-time task to pull all games for a season from CFBD API.
    Sets the games_pulled flag on completion.
    
    Args:
        season_year: Year of the season
        season_type: 'regular' or 'postseason'
        force: If True, pull even if already pulled
    """
    try:
        season = Season.objects.get(year=season_year)
        
        # Check if already pulled
        if season.games_pulled and not force:
            logger.info(f"Games already pulled for {season_year}, skipping")
            return
        
        # Check if teams have been pulled first
        if not season.teams_pulled:
            logger.warning(f"Teams not pulled for {season_year} yet, pulling teams first")
            pull_season_teams(season_year)
        
        logger.info(f"Pulling {season_type} games data for {season_year} season")
        
        # Get CFBD client
        cfbd_client = get_cfbd_client()
        
        # Fetch all games for the season
        games_data = cfbd_client.fetch_all_season_games(season_year, season_type)
        
        if not games_data:
            logger.error(f"No games data returned from CFBD for {season_year}")
            return
        
        logger.info(f"Processing {len(games_data)} games")
        
        created_count = 0
        updated_count = 0
        skipped_count = 0
        
        # Create team lookup by name for faster matching
        teams_by_name = {team.name: team for team in season.teams.all()}
        
        for game_data in games_data:
            week = Week.objects.get(season=season, season_type=season_type, number=game_data.get('week'))
            
            if not week:
                logger.warning(f"Week {game_data.get('week')} not found for {season_year} {season_type}")
                continue
            
            try:
                # Extract game fields (CFBD uses camelCase)
                game_id = game_data.get('id')
                season_type_value = game_data.get('seasonType', 'regular')  # camelCase!
                home_team_name = game_data.get('homeTeam')  # camelCase!
                away_team_name = game_data.get('awayTeam')  # camelCase!
                neutral_site = game_data.get('neutralSite', False)  # camelCase!
                conference_game = game_data.get('conferenceGame', False)  # camelCase!
                attendance = game_data.get('attendance')
                venue = game_data.get('venue')
                venue_id = game_data.get('venueId')  # camelCase!
                home_points = game_data.get('homePoints')  # camelCase!
                away_points = game_data.get('awayPoints')  # camelCase!
                completed = game_data.get('completed', False)
                
                # Parse kickoff time
                start_date_str = game_data.get('startDate')  # camelCase!
                if not start_date_str:
                    logger.warning(f"Game {game_id} has no start_date, skipping")
                    skipped_count += 1
                    continue
                
                try:
                    kickoff = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
                    # Ensure timezone-aware
                    if timezone.is_naive(kickoff):
                        kickoff = timezone.make_aware(kickoff)
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Invalid start_date for game {game_id}: {e}")
                    skipped_count += 1
                    continue
                
                # Find teams (create FCS teams on the fly if needed)
                home_team = teams_by_name.get(home_team_name)
                away_team = teams_by_name.get(away_team_name)
                
                # If a team is missing, create it (likely an FCS team)
                # But only if it's FBS or FCS classification
                if not home_team:
                    home_classification = game_data.get('homeClassification', 'fcs')
                    if home_classification not in ('fbs', 'fcs'):
                        skipped_count += 1
                        continue
                    home_team = Team.objects.create(
                        season=season,
                        name=home_team_name,
                        classification=home_classification,
                        conference=game_data.get('homeConference', ''),
                        abbreviation=home_team_name[:4].upper(),
                    )
                    teams_by_name[home_team_name] = home_team
                    logger.info(f"Created FCS team: {home_team_name}")
                
                if not away_team:
                    away_classification = game_data.get('awayClassification', 'fcs')
                    if away_classification not in ('fbs', 'fcs'):
                        skipped_count += 1
                        continue
                    away_team = Team.objects.create(
                        season=season,
                        name=away_team_name,
                        classification=away_classification,
                        conference=game_data.get('awayConference', ''),
                        abbreviation=away_team_name[:4].upper(),
                    )
                    teams_by_name[away_team_name] = away_team
                    logger.info(f"Created FCS team: {away_team_name}")
                
                # Only store games where at least one team is FBS
                if home_team.classification != 'fbs' and away_team.classification != 'fbs':
                    skipped_count += 1
                    continue
                
                # Create or update game
                game, created = Game.objects.update_or_create(
                    season=season,
                    external_id=str(game_id) if game_id else None,
                    defaults={
                        'week': week,
                        'season_type': season_type_value,
                        'home_team': home_team,
                        'away_team': away_team,
                        'kickoff': kickoff,
                        'neutral_site': neutral_site,
                        'conference_game': conference_game,
                        'attendance': attendance,
                        'venue_name': venue or '',
                        'venue_id': venue_id,
                        'home_score': home_points,
                        'away_score': away_points,
                        'is_final': completed,
                    }
                )
                
                if created:
                    created_count += 1
                else:
                    updated_count += 1
            
            except Exception as e:
                logger.error(f"Error processing game data: {e}", exc_info=True)
                continue
        
        # Mark as pulled
        season.games_pulled = True
        season.save(update_fields=['games_pulled'])
        
        logger.info(
            f"Games pull complete for {season_year}: "
            f"{created_count} created, {updated_count} updated, {skipped_count} skipped"
        )
    
    except Season.DoesNotExist:
        logger.error(f"Season {season_year} not found")
    except Exception as e:
        logger.error(f"Error pulling games for {season_year}: {e}", exc_info=True)


@shared_task(name='cfb.tasks.initialize_season')
def initialize_season(season_year: int, force: bool = False):
    """
    Master task to initialize a complete season.
    Pulls calendar, teams and games in sequence.
    
    Args:
        season_year: Year of the season
        force: If True, re-pull even if already pulled
    """
    try:
        # Get or create season
        season, created = Season.objects.get_or_create(
            year=season_year,
            defaults={'name': f'{season_year} Season', 'is_active': True}
        )
        
        if created:
            logger.info(f"Created new season: {season_year}")
        
        logger.info(f"Initializing season {season_year}")
        
        # Step 1: Pull calendar
        logger.info("Step 1: Pulling calendar...")
        pull_calendar(season_year, force=force)
        
        # Step 2: Pull teams
        if not season.teams_pulled or force:
            logger.info("Step 2: Pulling teams...")
            pull_season_teams(season_year, force=force)
        else:
            logger.info("Step 2: Teams already pulled, skipping")
        
        # Step 3: Pull games
        if not season.games_pulled or force:
            logger.info("Step 3: Pulling games...")
            pull_season_games(season_year, season_type='regular', force=force)
        else:
            logger.info("Step 3: Games already pulled, skipping")
        
        logger.info(f"Season {season_year} initialization complete!")
        
        # Print summary
        season.refresh_from_db()
        team_count = season.teams.count()
        game_count = season.games.count()
        logger.info(f"Summary: {team_count} teams, {game_count} games")
    
    except Exception as e:
        logger.error(f"Error initializing season {season_year}: {e}", exc_info=True)
