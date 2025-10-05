# Quick Reference - Game Auto-Update System

## One-Page Cheat Sheet

### Start the System (4 Terminal Windows)

```bash
# Terminal 1: Redis
docker run -d -p 6379:6379 redis:latest
# Or: redis-server

# Terminal 2: Django
cd pickem
python manage.py runserver

# Terminal 3: Celery Worker (OS-aware)
cd pickem
python manage.py start_celery_worker
# Or use the script:
#   Windows: start_celery.bat
#   Linux/Mac: ./start_celery.sh

# Terminal 4: Celery Beat (OS-aware)
cd pickem
python manage.py start_celery_beat
# Or use the script:
#   Windows: start_beat.bat
#   Linux/Mac: ./start_beat.sh
```

### Quick Start Scripts

**Windows:**
```cmd
cd pickem
start_celery.bat  # Worker
start_beat.bat    # Scheduler
```

**Linux/Mac:**
```bash
cd pickem
chmod +x start_celery.sh start_beat.sh  # First time only
./start_celery.sh  # Worker
./start_beat.sh    # Scheduler
```

### Essential Commands

```bash
# Start Celery worker (OS-aware)
python manage.py start_celery_worker

# Start Celery beat scheduler (OS-aware)
python manage.py start_celery_beat

# Test Celery setup
python manage.py test_celery

# Check system health
python manage.py check_system_status

# Manual poll ESPN
python manage.py poll_espn_now

# Sync games for date range
python manage.py sync_espn_games 2024 --start-date 2024-08-24 --end-date 2024-09-30

# Reset circuit breaker
python manage.py reset_circuit_breaker

# Update single game
python manage.py update_game <game_id>
```

### API Endpoints

```bash
# All games
curl "http://localhost:8000/api/games/"

# Live games only
curl "http://localhost:8000/api/games/live/"

# Games on specific date
curl "http://localhost:8000/api/games/?date=2024-09-28"

# System status
curl "http://localhost:8000/api/system/status/"

# Upcoming games (next 7 days)
curl "http://localhost:8000/api/games/upcoming/"
```

### Configuration (settings.py)

```python
# Polling: Optimized to only poll when active games exist

# Error handling
ESPN_API_MAX_RETRIES = 3
ESPN_API_CIRCUIT_BREAKER_THRESHOLD = 5
ESPN_API_CIRCUIT_BREAKER_TIMEOUT = 300
```

### Environment Variables (.env)

```env
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
REDIS_CACHE_URL=redis://localhost:6379/1
# Polling is now optimized automatically
```

### Redis Keys

```
scores:game:{external_id}      # Individual game cache
scores:live_state              # Live game tracking
scores:circuit_breaker         # Circuit breaker state
scores:last_poll               # Last poll timestamp
```

### Troubleshooting Quick Fixes

```bash
# Redis not connecting
redis-cli ping  # Should return PONG

# Circuit breaker stuck open
python manage.py reset_circuit_breaker

# No updates happening
python manage.py check_system_status  # Check all components

# Test ESPN connectivity
curl "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"

# Restart Celery
# Ctrl+C in worker/beat terminals, then restart
```

### File Locations

```
pickem/cfb/services/espn_api.py      # ESPN client
pickem/cfb/tasks.py                  # Celery tasks
pickem/cfb/api_views.py              # API endpoints
pickem/pickem/celery.py              # Celery config
pickem/pickem/settings.py            # Configuration
```

### Management Commands Location

```
pickem/cfb/management/commands/
  ├── check_system_status.py
  ├── poll_espn_now.py
  ├── sync_espn_games.py
  ├── update_game.py
  └── reset_circuit_breaker.py
```

### Health Check Indicators

✅ **Healthy System:**
- Circuit breaker: CLOSED
- Live state: Updated within last 3 minutes
- Last poll: Within last 5 minutes
- Cache: Accessible
- All 4 processes running

⚠️ **Needs Attention:**
- Circuit breaker: OPEN
- No recent polls
- Cache inaccessible
- Worker or beat not running

### Initial Setup Checklist

- [ ] Install dependencies: `pip install -r requirements.txt`
- [ ] Redis running: `redis-cli ping`
- [ ] Create active season in Django admin or shell
- [ ] Start Celery worker
- [ ] Start Celery beat
- [ ] Sync initial games: `python manage.py sync_espn_games 2024`
- [ ] Verify: `python manage.py check_system_status`
- [ ] Test API: `curl http://localhost:8000/api/system/status/`

### Frontend Integration (JavaScript)

```javascript
// Simple polling example
async function pollLiveGames() {
  const response = await fetch('/api/games/live/');
  const data = await response.json();
  updateScoreboard(data.games);
  
  // Adjust interval based on live games
  const interval = data.count > 0 ? 30000 : 300000;
  setTimeout(pollLiveGames, interval);
}

pollLiveGames();
```

### Monitoring Commands

```bash
# Watch worker logs
tail -f celery_worker.log

# Watch beat logs
tail -f celery_beat.log

# Monitor Redis
redis-cli monitor

# Check task queue
celery -A pickem inspect active
```

### Common Issues

| Issue | Solution |
|-------|----------|
| No updates | Check all 4 processes are running |
| Circuit breaker open | Wait 5 min or run `reset_circuit_breaker` |
| Redis errors | Verify Redis is running: `redis-cli ping` |
| Import errors | Make sure you're in `pickem/` directory |
| Games not found | Run `sync_espn_games` to fetch from ESPN |

### Performance Metrics

- **Polling frequency**: 60s (live) / 300s (normal)
- **API cache**: 30-120 seconds
- **Game cache**: 2 minutes
- **Circuit breaker**: Opens after 5 failures
- **Retry attempts**: 3 per request
- **Max API results**: 500 games

### Documentation Files

1. **SETUP_AUTO_UPDATE.md** - Complete setup guide
2. **GAME_AUTO_UPDATE.md** - Full system documentation
3. **API_EXAMPLES.md** - API usage examples
4. **IMPLEMENTATION_SUMMARY.md** - Technical overview
5. **QUICK_REFERENCE.md** - This file

### Support

- Check logs in Celery worker/beat terminals
- Run `check_system_status` for health check
- See `GAME_AUTO_UPDATE.md` for troubleshooting
- Test ESPN API manually with curl

---

**Pro Tip**: Keep `check_system_status` open in a terminal and refresh periodically to monitor system health.

