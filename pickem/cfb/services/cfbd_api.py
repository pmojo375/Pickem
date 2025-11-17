"""
CFBD API client for fetching teams and games data.
Handles fetching, caching, and storing JSON responses for debugging.
"""
import logging
import json
import os
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)


class CFBDAPIClient:
    """
    College Football Data API client.
    Handles fetching teams and games data with JSON response caching.
    """
    
    BASE_URL = "https://api.collegefootballdata.com"
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.CFBD_API_KEY
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json',
        })
        self.timeout = 30
        
        # Set up data directory for JSON storage
        self.data_dir = Path(settings.BASE_DIR) / 'cfbd_data'
        self.data_dir.mkdir(exist_ok=True)
    
    def _save_json_response(self, endpoint: str, params: Dict[str, Any], data: Any) -> str:
        """
        Save JSON response to file for debugging.
        
        Args:
            endpoint: API endpoint name
            params: Request parameters
            data: Response data
            
        Returns:
            Path to saved file
        """
        try:
            # Create filename from endpoint and params
            param_str = '_'.join(f"{k}={v}" for k, v in sorted(params.items()))
            timestamp = datetime.now().strftime('%Y%m%d')
            filename = f"{endpoint}_{param_str}_{timestamp}.json"
            
            # Clean filename
            filename = filename.replace('/', '_').replace('?', '_')
            filepath = self.data_dir / filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            
            logger.info(f"Saved CFBD response to {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Error saving JSON response: {e}")
            return ""
    
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Make request to CFBD API.
        
        Args:
            endpoint: API endpoint (e.g., '/teams/fbs')
            params: Query parameters
            
        Returns:
            JSON response data or None on failure
        """
        if not self.api_key:
            logger.error("CFBD API key not configured")
            return None
        
        url = f"{self.BASE_URL}{endpoint}"
        params = params or {}
        
        try:
            logger.info(f"Making CFBD API request to {endpoint} with params {params}")
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            
            # Save response to file
            self._save_json_response(endpoint.strip('/'), params, data)
            
            return data
        
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("CFBD API authentication failed - check API key")
            elif e.response.status_code == 429:
                logger.warning("CFBD API rate limit hit")
            else:
                logger.error(f"CFBD API HTTP error: {e}")
            return None
        
        except requests.exceptions.RequestException as e:
            logger.error(f"CFBD API request failed: {e}")
            return None
        
        except json.JSONDecodeError as e:
            logger.error(f"CFBD API response JSON decode error: {e}")
            return None
    
    def fetch_teams(self, year: int) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch all FBS and FCS teams for a given year.
        
        Args:
            year: Season year
            
        Returns:
            List of team dictionaries or None on failure
        """
        cache_key = f"cfbd:teams:{year}"
        
        # Check cache first
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Using cached CFBD teams data for {year}")
            return cached_data
        
        # Fetch from API
        data = self._make_request('/teams', {'year': year})
        
        if data:
            # Cache for 24 hours
            cache.set(cache_key, data, timeout=86400)
            logger.info(f"Fetched {len(data)} teams from CFBD for {year}")
        
        return data
    
    def fetch_games(
        self, 
        year: int, 
        season_type: str = 'regular',
        week: Optional[int] = None,
        division: str = 'fbs'
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch games for a given year and optional week.
        
        Args:
            year: Season year
            season_type: 'regular', 'postseason', or 'both'
            week: Optional week number (if None, fetches all weeks)
            division: 'fbs' or 'fcs'
            
        Returns:
            List of game dictionaries or None on failure
        """
        params = {
            'year': year,
            'seasonType': season_type,
            'division': division,
        }
        
        if week is not None:
            params['week'] = week
        
        cache_key = f"cfbd:games:{year}:{season_type}:{week or 'all'}:{division}"
        
        # Check cache first
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Using cached CFBD games data")
            return cached_data
        
        # Fetch from API
        data = self._make_request('/games', params)
        
        if data:
            # Cache for 1 hour
            cache.set(cache_key, data, timeout=3600)
            logger.info(f"Fetched {len(data)} games from CFBD")
        
        return data
    
    def fetch_all_season_games(self, year: int, season_type: str = 'regular') -> Optional[List[Dict[str, Any]]]:
        """
        Fetch all games for an entire season (all weeks).
        
        Args:
            year: Season year
            season_type: 'regular' or 'postseason'
            
        Returns:
            List of all game dictionaries or None on failure
        """
        logger.info(f"Fetching all {season_type} season games for {year}")
        
        # Fetch without week parameter to get all games
        all_games = self.fetch_games(year, season_type=season_type, week=None)
        
        if all_games:
            logger.info(f"Fetched total of {len(all_games)} games for {year} {season_type} season")
        
        return all_games
    
    
    def fetch_lines(
        self,
        year: int,
        week: Optional[int] = None,
        season_type: str = 'regular',
        team: Optional[str] = None
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch betting lines/spreads for games.
        
        Args:
            year: Season year
            week: Optional week number
            season_type: 'regular' or 'postseason'
            team: Optional team name to filter
            
        Returns:
            List of game line dictionaries or None on failure
        """
        params = {
            'year': year,
            'seasonType': season_type,
        }
        
        if week is not None:
            params['week'] = week
        
        if team:
            params['team'] = team
        
        cache_key = f"cfbd:lines:{year}:{season_type}:{week or 'all'}:{team or 'all'}"
        
        # Check cache first
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Using cached CFBD lines data")
            return cached_data
        
        # Fetch from API
        data = self._make_request('/lines', params)
        
        if data:
            # Cache for 1 hour
            cache.set(cache_key, data, timeout=3600)
            logger.info(f"Fetched {len(data)} game lines from CFBD")
        
        return data
    
    def fetch_rankings(
        self,
        year: int,
        week: Optional[int] = None,
        season_type: str = 'regular'
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch poll rankings for a season.
        
        Args:
            year: Season year
            week: Optional week number (if None, fetches all weeks)
            season_type: 'regular' or 'postseason'
            
        Returns:
            List of ranking dictionaries or None on failure
        """
        params = {
            'year': year,
            'seasonType': season_type,
        }
        
        if week is not None:
            params['week'] = week
        
        cache_key = f"cfbd:rankings:{year}:{season_type}:{week or 'all'}"
        
        # Check cache first
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Using cached CFBD rankings data")
            return cached_data
        
        # Fetch from API
        data = self._make_request('/rankings', params)
        
        if data:
            # Cache for 1 hour
            cache.set(cache_key, data, timeout=3600)
            logger.info(f"Fetched {len(data)} weeks of rankings from CFBD")
        
        return data
    
    def fetch_calendar(self, year: int) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch all weeks for a given year.
        """
        params = {'year': year}
        
        cache_key = f"cfbd:calendar:{year}"
        
        # Check cache first
        cached_data = cache.get(cache_key)
        if cached_data:
            logger.info(f"Using cached CFBD calendar data")
            return cached_data
        
        data = self._make_request('/calendar', params)
        
        if data:
            # Cache for 1 week
            cache.set(cache_key, data, timeout=604800)
            logger.info(f"Fetched {len(data)} calendar data from CFBD")
    
    def fetch_season_stats(
        self,
        year: int,
        end_week: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch cumulative team season statistics from week 0 through end_week - 1.
        Uses end_week - 1 to get stats through the last completed week.
        
        Args:
            year: Season year
            end_week: Current week number (will use end_week - 1 as the last completed week)
            
        Returns:
            List of team stat dictionaries or None on failure
        """
        # Use end_week - 1 to get stats through the last completed week
        last_completed_week = max(0, end_week - 1)
        
        params = {
            'year': year,
            'startWeek': 0,
            'endWeek': last_completed_week,
        }
        
        cache_key = f"cfbd:stats:season:{year}:0-{last_completed_week}"
        
        # Check cache first (gracefully handle Redis connection errors)
        try:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.info(f"Using cached CFBD season stats data for weeks 0-{last_completed_week}")
                # Still process cached data to ensure database is up to date
                self._process_and_save_stats(cached_data, year)
                return cached_data
        except Exception as e:
            logger.warning(f"Cache unavailable, skipping cache check: {e}")
        
        # Fetch from API
        data = self._make_request('/stats/season', params)
        
        if data:
            # Cache for 1 hour (gracefully handle Redis connection errors)
            try:
                cache.set(cache_key, data, timeout=3600)
            except Exception as e:
                logger.warning(f"Cache unavailable, skipping cache write: {e}")
            logger.info(f"Fetched {len(data)} team stats from CFBD for weeks 0-{last_completed_week}")
            
            # Process and save stats to database
            self._process_and_save_stats(data, year)
        
        return data
    
    def _process_and_save_stats(self, stats_data: List[Dict[str, Any]], year: int) -> None:
        """
        Process stats data from API and create/update TeamStat objects.
        Uses bulk operations for better performance with large datasets.
        
        Args:
            stats_data: List of stat dictionaries from API
            year: Season year
        """
        from ..models import Season, Team, TeamStat
        
        try:
            season = Season.objects.get(year=year)
        except Season.DoesNotExist:
            logger.error(f"Season {year} not found, cannot save stats")
            return
        
        # Pre-load all teams into a dictionary for O(1) lookup
        # Use both exact name and normalized name as keys
        teams_by_name = {}
        teams_by_name_normalized = {}
        all_teams = Team.objects.filter(season=season).select_related()
        
        for team in all_teams:
            # Exact name match (case-insensitive)
            teams_by_name[team.name.lower()] = team
            # Also store normalized versions for fuzzy matching
            teams_by_name_normalized[team.name.lower().strip()] = team
        
        logger.info(f"Pre-loaded {len(teams_by_name)} teams for season {year}")
        
        # Process all stat entries and build lists for bulk operations
        stats_to_create = []
        stats_to_update = []
        teams_not_found = set()
        
        # Get existing TeamStat records in bulk to check what needs updating
        existing_stats = {}
        existing_stat_keys = set()
        
        # Process stat entries and build lookup keys
        valid_entries = []
        
        for stat_entry in stats_data:
            team_name = stat_entry.get('team')
            stat_name = stat_entry.get('statName')
            stat_value = stat_entry.get('statValue')
            
            if not team_name or not stat_name or stat_value is None:
                logger.warning(f"Skipping invalid stat entry: {stat_entry}")
                continue
            
            # Find team using pre-loaded dictionary
            team = None
            team_name_lower = team_name.lower().strip()
            
            # Try exact match first
            team = teams_by_name.get(team_name_lower)
            
            # Try normalized match
            if not team:
                team = teams_by_name_normalized.get(team_name_lower)
            
            # Try fuzzy matching if still not found
            if not team:
                for db_team_name, db_team in teams_by_name_normalized.items():
                    if self._teams_match_for_stats(db_team_name, team_name_lower):
                        team = db_team
                        break
            
            if not team:
                teams_not_found.add(team_name)
                continue
            
            # Build lookup key for existing stats
            stat_key = (season.id, team.id, stat_name)
            existing_stat_keys.add(stat_key)
            
            valid_entries.append({
                'team': team,
                'stat_name': stat_name,
                'stat_value': float(stat_value),
                'key': stat_key
            })
        
        # Fetch existing stats in one query
        # Get all existing stats for this season to check what needs updating
        if existing_stat_keys:
            # Fetch all existing stats for teams that might have stats
            team_ids = {key[1] for key in existing_stat_keys}
            existing_stats_qs = TeamStat.objects.filter(
                season=season,
                team_id__in=team_ids
            ).select_related('team')
            
            for existing_stat in existing_stats_qs:
                key = (existing_stat.season_id, existing_stat.team_id, existing_stat.stat)
                if key in existing_stat_keys:
                    existing_stats[key] = existing_stat
        
        logger.info(f"Found {len(existing_stats)} existing stats, processing {len(valid_entries)} stat entries")
        
        # Separate into create and update lists
        for entry in valid_entries:
            key = entry['key']
            
            if key in existing_stats:
                # Update existing stat
                existing_stat = existing_stats[key]
                existing_stat.value = entry['stat_value']
                stats_to_update.append(existing_stat)
            else:
                # Create new stat
                stats_to_create.append(
                    TeamStat(
                        season=season,
                        team=entry['team'],
                        stat=entry['stat_name'],
                        value=entry['stat_value']
                    )
                )
        
        # Bulk operations
        with transaction.atomic():
            if stats_to_create:
                TeamStat.objects.bulk_create(stats_to_create, ignore_conflicts=True)
                logger.info(f"Bulk created {len(stats_to_create)} new stats")
            
            if stats_to_update:
                TeamStat.objects.bulk_update(stats_to_update, ['value'], batch_size=1000)
                logger.info(f"Bulk updated {len(stats_to_update)} existing stats")
        
        logger.info(
            f"Processed team stats for {year}: "
            f"{len(stats_to_create)} created, {len(stats_to_update)} updated"
        )
        
        if teams_not_found:
            logger.warning(
                f"Could not find {len(teams_not_found)} teams in database: "
                f"{', '.join(list(teams_not_found)[:10])}"
                f"{'...' if len(teams_not_found) > 10 else ''}"
            )
    
    def _find_team_for_stats(self, season, team_name: str):
        """
        Find a team in the database by name for stats processing.
        
        Args:
            season: Season object
            team_name: Team name from API
            
        Returns:
            Team object or None
        """
        from ..models import Team
        
        # Try exact name match (case-insensitive)
        team = Team.objects.filter(
            season=season,
            name__iexact=team_name
        ).first()
        
        if team:
            return team
        
        # Try fuzzy matching
        teams = Team.objects.filter(season=season)
        for t in teams:
            if self._teams_match_for_stats(t.name, team_name):
                return t
        
        return None
    
    def _teams_match_for_stats(self, db_name: str, api_name: str) -> bool:
        """
        Check if team names match with fuzzy logic.
        
        Args:
            db_name: Team name from database
            api_name: Team name from API
            
        Returns:
            True if names match
        """
        db_normalized = db_name.lower().strip()
        api_normalized = api_name.lower().strip()
        
        # Exact match
        if db_normalized == api_normalized:
            return True
        
        # One contains the other
        if db_normalized in api_normalized or api_normalized in db_normalized:
            return True
        
        # Check word overlap
        db_words = set(db_normalized.split())
        api_words = set(api_normalized.split())
        common_words = db_words & api_words
        
        # At least 1 word in common for short names, 2 for longer names
        if len(db_words) <= 2 or len(api_words) <= 2:
            return len(common_words) >= 1
        return len(common_words) >= 2


# Singleton instance
_cfbd_client = None


def get_cfbd_client() -> CFBDAPIClient:
    """Get or create singleton CFBD API client instance."""
    global _cfbd_client
    if _cfbd_client is None:
        _cfbd_client = CFBDAPIClient()
    return _cfbd_client

