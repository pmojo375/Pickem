"""
Management command to fetch and store spreads from CFBD API.
Updates GameSpread model and Game model's spread fields.
"""
from decimal import Decimal
from typing import Dict, Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from cfb.models import Season, Game, GameSpread
from cfb.services.cfbd_api import get_cfbd_client


class Command(BaseCommand):
    help = 'Fetch and store spreads from CFBD API for a specific week and season'

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
            '--provider',
            type=str,
            default='consensus',
            help='Preferred spread provider (default: consensus)',
        )

    def handle(self, *args, **options):
        season_year = options['season']
        week = options['week']
        season_type = options['season_type']
        preferred_provider = options['provider']
        
        # Verify season exists
        try:
            season = Season.objects.get(year=season_year)
        except Season.DoesNotExist:
            raise CommandError(f'Season {season_year} does not exist')

        self.stdout.write(
            self.style.WARNING(
                f'Fetching spreads from CFBD for {season_year} Week {week} ({season_type})...'
            )
        )

        # Fetch lines from CFBD
        cfbd_client = get_cfbd_client()
        lines_data = cfbd_client.fetch_lines(
            year=season_year,
            week=week,
            season_type=season_type
        )
        
        if not lines_data:
            raise CommandError('Failed to fetch lines data from CFBD API')

        self.stdout.write(f'Fetched {len(lines_data)} games with lines from CFBD')

        # Process each game's lines
        games_updated = 0
        spreads_created = 0
        games_not_found = 0
        games_no_lines = 0

        for game_data in lines_data:
            result = self._process_game_lines(
                game_data,
                season,
                week,
                season_type,
                preferred_provider
            )
            
            if result == 'updated':
                games_updated += 1
                spreads_created += 1
            elif result == 'not_found':
                games_not_found += 1
            elif result == 'no_lines':
                games_no_lines += 1

        # Print summary
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSummary:\n'
                f'  Games updated: {games_updated}\n'
                f'  Spreads created: {spreads_created}\n'
                f'  Games not found in DB: {games_not_found}\n'
                f'  Games with no lines: {games_no_lines}'
            )
        )

    def _process_game_lines(
        self,
        game_data: Dict,
        season: Season,
        week: int,
        season_type: str,
        preferred_provider: str
    ) -> str:
        """
        Process lines for a single game.
        
        Returns:
            'updated' if successful, 'not_found' if game doesn't exist,
            'no_lines' if no lines available
        """
        home_team_name = game_data.get('homeTeam')
        home_team_id = game_data.get('homeTeamId')
        away_team_name = game_data.get('awayTeam')
        lines = game_data.get('lines', [])

        if not lines:
            self.stdout.write(
                self.style.WARNING(
                    f'  No lines for {away_team_name} @ {home_team_name}'
                )
            )
            return 'no_lines'

        # Find the game in our database
        game = self._find_game(season, week, season_type, home_team_name, away_team_name)
        
        if not game:
            self.stdout.write(
                self.style.WARNING(
                    f'  Game not found in DB: {away_team_name} @ {home_team_name}'
                )
            )
            return 'not_found'

        # Extract best spread from lines
        spread_data = self._extract_best_spread(lines, preferred_provider)
        
        if not spread_data:
            self.stdout.write(
                self.style.WARNING(
                    f'  No valid spread found for {away_team_name} @ {home_team_name}'
                )
            )
            return 'no_lines'

        # Update the game and create spread record
        self._update_game_spreads(game, spread_data)
        
        self.stdout.write(
            self.style.SUCCESS(
                f'  ✓ {away_team_name} @ {home_team_name}: '
                f'{spread_data["provider"]} spread Home:{spread_data["home_spread"]}/Away:{spread_data["away_spread"]}'
            )
        )
        
        return 'updated'

    def _find_game(
        self,
        season: Season,
        week: int,
        season_type: str,
        home_team_name: str,
        away_team_name: str
    ) -> Optional[Game]:
        """
        Find a game in the database by season, week, and team names.
        Uses fuzzy matching for team names.
        """
        # First try exact match
        game = Game.objects.filter(
            season=season,
            week=week,
            season_type=season_type,
            home_team__name__iexact=home_team_name,
            away_team__name__iexact=away_team_name
        ).first()
        
        if game:
            return game

        # Try fuzzy matching - find games in this week and match team names
        games = Game.objects.filter(
            season=season,
            week=week,
            season_type=season_type
        ).select_related('home_team', 'away_team')

        for g in games:
            if (self._teams_match(g.home_team.name, home_team_name) and
                self._teams_match(g.away_team.name, away_team_name)):
                return g

        return None

    def _teams_match(self, db_name: str, api_name: str) -> bool:
        """
        Check if team names match with fuzzy logic.
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

    def _extract_best_spread(
        self,
        lines: list,
        preferred_provider: str
    ) -> Optional[Dict]:
        """
        Extract the best spread from available lines.
        Prefers the specified provider, falls back to first available.
        
        The spread in CFBD is always from the home team's perspective.
        Negative = home favored, Positive = away favored.
        
        Returns dict with home_spread, away_spread, spread_open, and provider, or None
        """
        # First, try to find the preferred provider
        for line in lines:
            provider = line.get('provider', '').lower()
            if preferred_provider.lower() in provider:
                spread = line.get('spread')
                spread_open = line.get('spreadOpen')
                
                if spread is not None:
                    parsed = self._parse_spread(spread, spread_open)
                    if parsed:
                        parsed['provider'] = line.get('provider', 'Unknown')
                        return parsed
        
        # Fall back to first line with a valid spread
        for line in lines:
            spread = line.get('spread')
            spread_open = line.get('spreadOpen')
            
            if spread is not None:
                parsed = self._parse_spread(spread, spread_open)
                if parsed:
                    parsed['provider'] = line.get('provider', 'Unknown')
                    return parsed
        
        return None

    def _parse_spread(
        self,
        spread: float,
        spread_open: Optional[float]
    ) -> Optional[Dict]:
        """
        Parse the spread to determine home/away spreads.
        
        The spread from CFBD is always from the home team's perspective:
        - Negative spread = home team is favored (e.g., -6)
        - Positive spread = away team is favored (e.g., +14.5)
        
        Args:
            spread: The spread value from home team's perspective
            spread_open: Opening spread (can be None)
            
        Returns:
            Dict with home_spread, away_spread, home_spread_open, away_spread_open
        """
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
        
        return {
            'home_spread': home_spread,
            'away_spread': away_spread,
            'home_spread_open': home_spread_open,
            'away_spread_open': away_spread_open,
        }

    def _update_game_spreads(self, game: Game, spread_data: Dict) -> None:
        """
        Update game spreads and create GameSpread record.
        
        Args:
            game: Game instance to update
            spread_data: Dict containing home_spread, away_spread, home_spread_open,
                        away_spread_open, and provider
        """
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

