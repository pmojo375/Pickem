"""
Debug command to troubleshoot live updates.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from cfb.models import Game, Season


class Command(BaseCommand):
    help = 'Debug live score updates'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('=== Live Updates Debugging ===\n'))

        # 1. Check active season
        self.stdout.write('1. Checking active season...')
        active_season = Season.objects.filter(is_active=True).first()
        if active_season:
            self.stdout.write(self.style.SUCCESS(f'   Active season: {active_season.year} - {active_season.name}'))
        else:
            self.stdout.write(self.style.ERROR('   No active season found!'))
            self.stdout.write('   Fix: Create an active season in Django admin or shell')
            return

        # 2. Check for games
        self.stdout.write('\n2. Checking games in database...')
        total_games = Game.objects.filter(season=active_season).count()
        self.stdout.write(f'   Total games in season: {total_games}')

        if total_games == 0:
            self.stdout.write(self.style.WARNING('   No games in database!'))
            self.stdout.write('   Fix: Run sync_espn_games to import games')
            return

        # 3. Check games with external_id
        games_with_id = Game.objects.filter(season=active_season, external_id__isnull=False).count()
        self.stdout.write(f'   Games with external_id: {games_with_id}/{total_games}')
        
        if games_with_id == 0:
            self.stdout.write(self.style.ERROR('   No games have external_id!'))
            self.stdout.write('   Fix: Games need external_id to match with ESPN')
            return

        # 4. Check recent games
        self.stdout.write('\n3. Checking recent games...')
        now = timezone.now()
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)
        
        recent_games = Game.objects.filter(
            season=active_season,
            kickoff__gte=yesterday,
            kickoff__lte=tomorrow
        ).order_by('kickoff')

        self.stdout.write(f'   Games in window ({yesterday.date()} to {tomorrow.date()}): {recent_games.count()}')

        if recent_games.exists():
            self.stdout.write('   Recent games:')
            for game in recent_games[:5]:
                status = 'FINAL' if game.is_final else (
                    f'Q{game.quarter} {game.clock}' if game.quarter else 'Not Started'
                )
                self.stdout.write(
                    f'     • {game.id}: {game.away_team.name} @ {game.home_team.name}'
                )
                self.stdout.write(
                    f'       Kickoff: {game.kickoff.strftime("%Y-%m-%d %H:%M %Z")}'
                )
                self.stdout.write(
                    f'       Status: {status}, External ID: {game.external_id or "None"}'
                )
        else:
            self.stdout.write(self.style.WARNING('   No recent games found!'))
            
            # Show the most recent game
            latest = Game.objects.filter(season=active_season).order_by('-kickoff').first()
            if latest:
                self.stdout.write(f'   Most recent game: {latest.kickoff.date()}')

        # 5. Check live games
        self.stdout.write('\n4. Checking for live games...')
        live_games = Game.objects.filter(
            season=active_season,
            kickoff__lte=now,
            is_final=False
        ).exclude(
            home_score__isnull=True,
            away_score__isnull=True
        )

        self.stdout.write(f'   Live games: {live_games.count()}')

        if live_games.exists():
            for game in live_games[:3]:
                self.stdout.write(
                    f'     - {game.away_team.abbreviation or game.away_team.name[:4]} '
                    f'{game.away_score or 0} @ '
                    f'{game.home_team.abbreviation or game.home_team.name[:4]} '
                    f'{game.home_score or 0} - Q{game.quarter or 0}'
                )

        # 6. Test API endpoint
        self.stdout.write('\n5. Testing API endpoint...')
        today = now.date()
        self.stdout.write(f'   Today\'s date: {today}')
        self.stdout.write(f'   Test URL: http://localhost:8000/api/games/?date={today}')
        
        # Simulate what the API returns
        games_for_today = Game.objects.filter(
            season=active_season,
            kickoff__date=today
        ).count()
        self.stdout.write(f'   Games scheduled for today: {games_for_today}')

        # 7. Check static files
        self.stdout.write('\n6. Checking static files...')
        import os
        from django.conf import settings
        
        js_file = os.path.join(settings.BASE_DIR, 'static', 'js', 'live-score-updater.js')
        css_file = os.path.join(settings.BASE_DIR, 'static', 'css', 'live-updates.css')
        
        if os.path.exists(js_file):
            self.stdout.write(self.style.SUCCESS(f'   JavaScript file exists'))
        else:
            self.stdout.write(self.style.ERROR(f'   JavaScript file missing!'))
            self.stdout.write(f'     Expected: {js_file}')

        if os.path.exists(css_file):
            self.stdout.write(self.style.SUCCESS(f'   CSS file exists'))
        else:
            self.stdout.write(self.style.ERROR(f'   CSS file missing!'))
            self.stdout.write(f'     Expected: {css_file}')

        # 8. Check Celery status
        self.stdout.write('\n7. Checking Celery polling...')
        from django.core.cache import cache
        
        last_poll = cache.get(settings.REDIS_KEY_LAST_POLL)
        if last_poll:
            import datetime
            last_poll_time = datetime.datetime.fromtimestamp(last_poll)
            time_since = now.timestamp() - last_poll
            self.stdout.write(f'   Last poll: {last_poll_time} ({time_since:.0f}s ago)')
        else:
            self.stdout.write(self.style.WARNING('   No recent poll recorded'))
            self.stdout.write('   Is Celery worker running?')

        live_state = cache.get(settings.REDIS_KEY_LIVE_STATE)
        if live_state:
            self.stdout.write(f'   Live state: {live_state}')
        else:
            self.stdout.write(self.style.WARNING('   No live state cached'))

        # Summary
        self.stdout.write(self.style.HTTP_INFO('\n=== Summary ==='))
        
        if games_for_today > 0:
            self.stdout.write(self.style.SUCCESS('Games exist for today'))
            self.stdout.write('\nNext Steps:')
            self.stdout.write('   1. Open /live/ page in browser')
            self.stdout.write('   2. Press F12 to open developer console')
            self.stdout.write('   3. Look for: [LiveScores] Auto-update initialized')
            self.stdout.write('   4. Check Network tab for /api/games requests')
            self.stdout.write('   5. Check Console tab for any errors')
        else:
            self.stdout.write(self.style.WARNING('No games scheduled for today'))
            self.stdout.write('\nTo fix:')
            self.stdout.write(f'   python manage.py sync_espn_games {active_season.year} --start-date {yesterday.date()} --end-date {tomorrow.date()}')

