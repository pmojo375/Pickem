"""
ESPN API client with circuit breaker, retry logic, and error handling.
Handles fetching live game data from ESPN's unofficial scoreboard API.
"""
import logging
import time
import random
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
from dataclasses import dataclass

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerState:
    """Tracks the state of the circuit breaker."""
    failure_count: int = 0
    last_failure_time: Optional[float] = None
    is_open: bool = False

    def record_success(self):
        """Reset circuit breaker on successful call."""
        self.failure_count = 0
        self.last_failure_time = None
        self.is_open = False

    def record_failure(self):
        """Record a failure and potentially open the circuit."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= settings.ESPN_API_CIRCUIT_BREAKER_THRESHOLD:
            self.is_open = True
            logger.warning(
                f"Circuit breaker opened after {self.failure_count} consecutive failures"
            )

    def can_attempt(self) -> bool:
        """Check if we can attempt a request."""
        if not self.is_open:
            return True
        
        # Check if enough time has passed to try closing the circuit
        if self.last_failure_time:
            time_since_failure = time.time() - self.last_failure_time
            if time_since_failure >= settings.ESPN_API_CIRCUIT_BREAKER_TIMEOUT:
                logger.info("Circuit breaker attempting to close, trying half-open state")
                self.is_open = False
                self.failure_count = 0
                return True
        
        return False


class ESPNAPIClient:
    """
    ESPN API client with built-in circuit breaker and retry logic.
    Handles rate limiting, error handling, and caching.
    """
    
    def __init__(self):
        self.base_url = settings.ESPN_SCOREBOARD_URL
        self.timeout = settings.ESPN_API_TIMEOUT
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CFB-Pickem/1.0',
            'Accept': 'application/json',
        })
        self._circuit_breaker = self._load_circuit_breaker_state()

    def _load_circuit_breaker_state(self) -> CircuitBreakerState:
        """Load circuit breaker state from Redis."""
        cached_state = cache.get(settings.REDIS_KEY_CIRCUIT_BREAKER)
        if cached_state:
            return CircuitBreakerState(**cached_state)
        return CircuitBreakerState()

    def _save_circuit_breaker_state(self):
        """Save circuit breaker state to Redis."""
        state_dict = {
            'failure_count': self._circuit_breaker.failure_count,
            'last_failure_time': self._circuit_breaker.last_failure_time,
            'is_open': self._circuit_breaker.is_open,
        }
        cache.set(
            settings.REDIS_KEY_CIRCUIT_BREAKER,
            state_dict,
            timeout=settings.ESPN_API_CIRCUIT_BREAKER_TIMEOUT * 2
        )

    def _add_jitter(self, wait_time: float) -> float:
        """Add random jitter to wait time to avoid thundering herd."""
        jitter = random.uniform(0, settings.ESPN_API_RETRY_JITTER)
        return wait_time + jitter

    @retry(
        stop=stop_after_attempt(settings.ESPN_API_MAX_RETRIES),
        wait=wait_exponential(
            multiplier=settings.ESPN_API_RETRY_BACKOFF_FACTOR,
            min=1,
            max=30
        ),
        retry=retry_if_exception_type((
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _make_request(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Make HTTP request to ESPN API with retry logic.
        
        Args:
            params: Query parameters for the request
            
        Returns:
            JSON response as dictionary
            
        Raises:
            requests.RequestException: On request failure after retries
        """
        try:
            response = self.session.get(
                self.base_url,
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # Rate limited
                logger.warning("ESPN API rate limit hit, backing off")
                # Use Retry-After header if present
                retry_after = e.response.headers.get('Retry-After')
                if retry_after:
                    time.sleep(int(retry_after))
            raise

    def fetch_scoreboard(
        self,
        date: Optional[datetime] = None,
        limit: int = 300
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch scoreboard data from ESPN API with circuit breaker protection.
        
        Args:
            date: Date to fetch games for (defaults to today)
            limit: Maximum number of games to return
            
        Returns:
            Scoreboard data dictionary or None if circuit is open or request fails
        """
        # Check circuit breaker
        if not self._circuit_breaker.can_attempt():
            logger.warning("Circuit breaker is open, skipping ESPN API call")
            return None

        try:
            params = {'limit': limit}
            if date:
                params['dates'] = date.strftime('%Y%m%d')

            logger.info(f"Fetching ESPN scoreboard for {date or 'today'}")
            data = self._make_request(params)
            
            # Record success
            self._circuit_breaker.record_success()
            self._save_circuit_breaker_state()
            
            return data

        except requests.RequestException as e:
            logger.error(f"ESPN API request failed: {e}")
            self._circuit_breaker.record_failure()
            self._save_circuit_breaker_state()
            return None

    def fetch_games_in_range(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> Dict[str, Dict[str, Any]]:
        """
        Fetch all games within a date range.
        
        Args:
            start_date: Start of date range
            end_date: End of date range
            
        Returns:
            Dictionary mapping game external_id to game data
        """
        all_games = {}
        current_date = start_date.date()
        end = end_date.date()

        while current_date <= end:
            # Add small delay between requests to respect rate limits
            if current_date != start_date.date():
                time.sleep(self._add_jitter(0.5))

            data = self.fetch_scoreboard(date=datetime.combine(current_date, datetime.min.time()))
            
            if data and 'events' in data:
                for event in data['events']:
                    event_id = str(event.get('id', ''))
                    if event_id:
                        all_games[event_id] = event

            current_date += timedelta(days=1)

        logger.info(f"Fetched {len(all_games)} games from ESPN API")
        return all_games

    def parse_game_data(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse ESPN event data into normalized game data structure.
        
        Args:
            event: Raw ESPN event data
            
        Returns:
            Normalized game data dictionary or None if parsing fails
        """
        try:
            event_id = str(event.get('id', ''))
            if not event_id:
                return None

            # Extract basic event info
            name = event.get('name', '')
            short_name = event.get('shortName', '')
            date_str = event.get('date', '')
            
            # Parse kickoff time
            kickoff = None
            if date_str:
                try:
                    kickoff = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass

            # Extract status
            status = event.get('status', {})
            status_type = status.get('type', {})
            status_state = status_type.get('state', 'pre')  # pre, in, post
            status_detail = status_type.get('detail', '')
            period = status.get('period', 0)
            display_clock = status.get('displayClock', '')

            # Determine if game is final
            is_final = status_state == 'post'
            is_live = status_state == 'in'

            # Extract competitions (usually one per event)
            competitions = event.get('competitions', [])
            if not competitions:
                return None

            competition = competitions[0]
            competitors = competition.get('competitors', [])

            # Find home and away teams
            home_data = None
            away_data = None
            for competitor in competitors:
                if competitor.get('homeAway') == 'home':
                    home_data = competitor
                elif competitor.get('homeAway') == 'away':
                    away_data = competitor

            if not home_data or not away_data:
                return None

            # Extract scores
            home_score = None
            away_score = None
            try:
                home_score = int(home_data.get('score', 0)) if is_live or is_final else None
                away_score = int(away_data.get('score', 0)) if is_live or is_final else None
            except (ValueError, TypeError):
                pass

            # Extract team info
            home_team = home_data.get('team', {})
            away_team = away_data.get('team', {})
            
            home_team_espn_id = str(home_team.get('id', ''))
            away_team_espn_id = str(away_team.get('id', ''))
            home_team_name = home_team.get('displayName', '')
            away_team_name = away_team.get('displayName', '')
            home_team_abbr = home_team.get('abbreviation', '')
            away_team_abbr = away_team.get('abbreviation', '')

            # Extract broadcast info
            broadcasts = competition.get('broadcasts', [])
            broadcast_network = broadcasts[0].get('names', [''])[0] if broadcasts else ''

            # Extract odds/spread (if available)
            odds = competition.get('odds', [])
            spread = None
            if odds:
                spread = odds[0].get('details', '')

            return {
                'external_id': event_id,
                'name': name,
                'short_name': short_name,
                'kickoff': kickoff,
                'status_state': status_state,
                'status_detail': status_detail,
                'is_final': is_final,
                'is_live': is_live,
                'period': period,
                'clock': display_clock,
                'home_score': home_score,
                'away_score': away_score,
                'home_team_espn_id': home_team_espn_id,
                'away_team_espn_id': away_team_espn_id,
                'home_team_name': home_team_name,
                'away_team_name': away_team_name,
                'home_team_abbr': home_team_abbr,
                'away_team_abbr': away_team_abbr,
                'broadcast_network': broadcast_network,
                'spread': spread,
            }

        except Exception as e:
            logger.error(f"Error parsing ESPN game data: {e}", exc_info=True)
            return None

    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status for monitoring."""
        return {
            'is_open': self._circuit_breaker.is_open,
            'failure_count': self._circuit_breaker.failure_count,
            'last_failure_time': self._circuit_breaker.last_failure_time,
            'can_attempt': self._circuit_breaker.can_attempt(),
        }


# Singleton instance
_espn_client = None


def get_espn_client() -> ESPNAPIClient:
    """Get or create singleton ESPN API client instance."""
    global _espn_client
    if _espn_client is None:
        _espn_client = ESPNAPIClient()
    return _espn_client

