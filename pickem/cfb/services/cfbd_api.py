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
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
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
        Fetch all FBS teams for a given year.
        
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
        data = self._make_request('/teams/fbs', {'year': year})
        
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
    
    def fetch_team_records(self, year: int, team: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch team records for a season.
        
        Args:
            year: Season year
            team: Optional team name to filter
            
        Returns:
            List of team record dictionaries
        """
        params = {'year': year}
        if team:
            params['team'] = team
        
        data = self._make_request('/records', params)
        
        if data:
            logger.info(f"Fetched records for {year}")
        
        return data
    
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


# Singleton instance
_cfbd_client = None


def get_cfbd_client() -> CFBDAPIClient:
    """Get or create singleton CFBD API client instance."""
    global _cfbd_client
    if _cfbd_client is None:
        _cfbd_client = CFBDAPIClient()
    return _cfbd_client

