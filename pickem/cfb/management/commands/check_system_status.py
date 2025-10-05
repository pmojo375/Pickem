"""
Management command to check the status of the auto-update system.
Shows circuit breaker state, polling info, and cache status.
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone

from cfb.services.espn_api import get_espn_client
from cfb.models import Game, Season


class Command(BaseCommand):
    help = 'Check the status of the game auto-update system'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('=== Game Auto-Update System Status ===\n'))

        # Active Season
        active_season = Season.objects.filter(is_active=True).first()
        if active_season:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Active Season: {active_season.year} - {active_season.name}')
            )
            total_games = Game.objects.filter(season=active_season).count()
            self.stdout.write(f'  Total games: {total_games}')
        else:
            self.stdout.write(self.style.ERROR('✗ No active season found'))

        # Circuit Breaker Status
        self.stdout.write(self.style.HTTP_INFO('\n--- Circuit Breaker Status ---'))
        espn_client = get_espn_client()
        cb_status = espn_client.get_circuit_breaker_status()
        
        if cb_status['is_open']:
            self.stdout.write(self.style.ERROR('✗ Circuit breaker is OPEN'))
            self.stdout.write(f'  Failure count: {cb_status["failure_count"]}')
            if cb_status['last_failure_time']:
                import datetime
                last_failure = datetime.datetime.fromtimestamp(cb_status['last_failure_time'])
                self.stdout.write(f'  Last failure: {last_failure}')
        else:
            self.stdout.write(self.style.SUCCESS('✓ Circuit breaker is CLOSED'))
            if cb_status['failure_count'] > 0:
                self.stdout.write(
                    self.style.WARNING(f'  Warning: {cb_status["failure_count"]} recent failures')
                )

        # Live State
        self.stdout.write(self.style.HTTP_INFO('\n--- Live Game State ---'))
        live_state = cache.get(settings.REDIS_KEY_LIVE_STATE)
        
        if live_state:
            has_live = live_state.get('has_live_games', False)
            live_count = live_state.get('live_game_count', 0)
            last_check = live_state.get('last_check', 'Unknown')
            
            if has_live:
                self.stdout.write(
                    self.style.SUCCESS(f'✓ {live_count} game(s) currently live')
                )
            else:
                self.stdout.write('  No games currently live')
            
            self.stdout.write(f'  Last check: {last_check}')
        else:
            self.stdout.write('  No live state cached (system may not have polled yet)')

        # Last Poll Time
        self.stdout.write(self.style.HTTP_INFO('\n--- Polling Info ---'))
        last_poll_ts = cache.get(settings.REDIS_KEY_LAST_POLL)
        
        if last_poll_ts:
            import datetime
            last_poll = datetime.datetime.fromtimestamp(last_poll_ts)
            time_since = timezone.now().timestamp() - last_poll_ts
            self.stdout.write(f'  Last poll: {last_poll} ({time_since:.0f}s ago)')
        else:
            self.stdout.write('  No recent poll recorded')

        self.stdout.write(f'  Polling intervals:')
        self.stdout.write(f'    Live games: {settings.GAME_POLL_INTERVAL_LIVE}s')
        self.stdout.write(f'    Normal: {settings.GAME_POLL_INTERVAL_NORMAL}s')
        self.stdout.write(f'    Offseason: {settings.GAME_POLL_INTERVAL_OFFSEASON}s')

        # Recent Games
        if active_season:
            self.stdout.write(self.style.HTTP_INFO('\n--- Recent Games ---'))
            now = timezone.now()
            from datetime import timedelta
            
            recent_games = Game.objects.filter(
                season=active_season,
                kickoff__gte=now - timedelta(days=2),
                kickoff__lte=now + timedelta(days=1)
            ).order_by('kickoff')[:10]

            if recent_games:
                for game in recent_games:
                    status = 'FINAL' if game.is_final else (
                        f'Q{game.quarter} {game.clock}' if game.quarter else 'Not Started'
                    )
                    score = f'{game.away_score or "-"} @ {game.home_score or "-"}'
                    self.stdout.write(
                        f'  {game.away_team.abbreviation or game.away_team.name[:4]} @ '
                        f'{game.home_team.abbreviation or game.home_team.name[:4]}: '
                        f'{score} - {status}'
                    )
            else:
                self.stdout.write('  No recent games found')

        # Cache Stats
        self.stdout.write(self.style.HTTP_INFO('\n--- Cache Status ---'))
        try:
            # Try to get a cache connection info
            self.stdout.write(f'  Cache backend: {settings.CACHES["default"]["BACKEND"]}')
            self.stdout.write(f'  Cache location: {settings.CACHES["default"]["LOCATION"]}')
            
            # Test cache connectivity
            test_key = 'system_status_test'
            cache.set(test_key, 'test', timeout=10)
            if cache.get(test_key) == 'test':
                self.stdout.write(self.style.SUCCESS('  ✓ Cache is accessible'))
                cache.delete(test_key)
            else:
                self.stdout.write(self.style.ERROR('  ✗ Cache test failed'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ✗ Cache error: {e}'))

        self.stdout.write(self.style.HTTP_INFO('\n=== End Status Report ==='))

