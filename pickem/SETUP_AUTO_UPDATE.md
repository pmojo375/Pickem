# Quick Setup Guide: Game Auto-Update System

This guide will help you get the game auto-update system running in 5 minutes.

## Prerequisites

- Python 3.10+
- Redis server
- Django project already set up

## Step-by-Step Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `celery[redis]>=5.4.0` - Background task processing
- `redis>=5.0.0` - Redis client
- `tenacity>=8.2.0` - Retry logic with backoff

### 2. Start Redis Server

**Option A: Using Docker (Recommended)**
```bash
docker run -d --name redis-pickem -p 6379:6379 redis:latest
```

**Option B: Local Installation**

**Windows:**
```bash
# Install via Chocolatey
choco install redis-64

# Or use WSL and follow Linux instructions
```

**Mac:**
```bash
brew install redis
redis-server
```

**Linux:**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

**Verify Redis is running:**
```bash
redis-cli ping
# Should return: PONG
```

### 3. Create and Activate Season

The system needs an active season to poll games:

```bash
python manage.py shell
```

```python
from cfb.models import Season

# Create season for current year
season = Season.objects.create(
    year=2024,
    name="2024 Season",
    is_active=True
)
print(f"Created season: {season}")
exit()
```

### 4. Start Celery Worker

Open a **new terminal window** and run:

```bash
cd pickem

# Windows
celery -A pickem worker --loglevel=info --pool=solo

# Linux/Mac (better performance)
celery -A pickem worker --loglevel=info --pool=prefork --concurrency=4
```

Keep this terminal open. You should see:
```
[tasks]
  . cfb.tasks.adjust_polling_interval
  . cfb.tasks.cleanup_old_game_cache
  . cfb.tasks.poll_espn_scores
  . cfb.tasks.sync_games_from_espn
  . cfb.tasks.update_single_game
```

### 5. Start Celery Beat (Scheduler)

Open **another new terminal window** and run:

```bash
cd pickem
celery -A pickem beat --loglevel=info
```

You should see:
```
LocalTime -> 2024-09-28 20:15:00
Configuration ->
    . broker -> redis://localhost:6379/0
    . loader -> celery.loaders.app.AppLoader
    . scheduler -> celery.beat.PersistentScheduler
```

### 6. Sync Initial Games (Optional but Recommended)

In your **main terminal**, run:

```bash
python manage.py sync_espn_games 2024 --start-date 2024-08-24 --end-date 2024-09-30
```

This will fetch and create games from ESPN for the specified date range. The first sync may take a minute or two.

### 7. Verify System Status

```bash
python manage.py check_system_status
```

You should see output like:
```
=== Game Auto-Update System Status ===

✓ Active Season: 2024 - 2024 Season
  Total games: 156

--- Circuit Breaker Status ---
✓ Circuit breaker is CLOSED

--- Live Game State ---
  8 game(s) currently live
  Last check: 2024-09-28T20:15:00Z

--- Polling Info ---
  Last poll: 2024-09-28 20:14:30 (30s ago)
  Polling intervals:
    Live games: 60s
    Normal: 300s
    Offseason: 3600s

✓ Cache is accessible
```

### 8. Test the API

With the Django server running (`python manage.py runserver`), test the endpoints:

```bash
# Get all games
curl "http://localhost:8000/api/games/"

# Get live games
curl "http://localhost:8000/api/games/live/"

# Get games for a specific date
curl "http://localhost:8000/api/games/?date=2024-09-28"

# Get system status
curl "http://localhost:8000/api/system/status/"
```

## Testing the System

### Manual Poll Test

Trigger a poll manually to see it working:

```bash
python manage.py poll_espn_now
```

Watch the output in your Celery worker terminal. You should see game updates.

### Single Game Update Test

If you have a specific game:

```bash
# Get a game ID first
python manage.py shell -c "from cfb.models import Game; print(Game.objects.first().id)"

# Update that game
python manage.py update_game <game_id>
```

## What Should Be Running

After setup, you should have **4 processes running**:

1. **Django development server** (`python manage.py runserver`)
2. **Redis server** (`redis-server` or Docker container)
3. **Celery worker** (`celery -A pickem worker`)
4. **Celery beat** (`celery -A pickem beat`)

## Terminal Layout

I recommend this terminal layout:

```
┌─────────────────────┬─────────────────────┐
│  Django Server      │  Celery Worker      │
│  (runserver)        │  (worker logs)      │
├─────────────────────┼─────────────────────┤
│  Celery Beat        │  Commands           │
│  (scheduler logs)   │  (your work here)   │
└─────────────────────┴─────────────────────┘
```

## Configuration

The system works out of the box, but you can customize settings in `settings.py`:

```python
# Polling: Optimized to only poll when active games exist

# Error handling
ESPN_API_MAX_RETRIES = 3
ESPN_API_CIRCUIT_BREAKER_THRESHOLD = 5
```

Or use environment variables in `.env`:

```env
# Polling is now optimized automatically
CELERY_BROKER_URL=redis://localhost:6379/0
```

## Common Issues

### "Connection refused" to Redis

**Problem**: Celery can't connect to Redis

**Solution**:
```bash
# Check Redis is running
redis-cli ping

# If not running, start it
# Docker: docker start redis-pickem
# Local: redis-server
```

### Celery worker won't start

**Problem**: Import errors or module not found

**Solution**:
```bash
# Make sure you're in the pickem directory
cd pickem

# Make sure virtual environment is activated
source venv/bin/activate  # Linux/Mac
.\venv\Scripts\activate   # Windows

# Reinstall dependencies
pip install -r ../requirements.txt
```

### No games being updated

**Problem**: System is running but no updates

**Checklist**:
1. Is there an active season? (`python manage.py check_system_status`)
2. Are there games in the database? (`python manage.py check_system_status`)
3. Do games have `external_id` set? (Required for ESPN matching)
4. Is the circuit breaker open? (`python manage.py check_system_status`)
5. Are all 4 processes running? (Django, Redis, Worker, Beat)

### Circuit breaker is open

**Problem**: Too many ESPN API failures

**Solution**:
```bash
# Reset the circuit breaker
python manage.py reset_circuit_breaker

# Try a manual poll
python manage.py poll_espn_now
```

## Next Steps

Once everything is running:

1. **Monitor the system**: Check `python manage.py check_system_status` periodically
2. **Build frontend**: Use the API endpoints to display live scores
3. **Customize polling**: Adjust intervals based on your needs
4. **Add monitoring**: Set up logging and alerting

## Production Deployment

For production, you'll want to:

1. Use a production WSGI server (Gunicorn, uWSGI)
2. Use a production Redis with persistence
3. Run Celery with supervisor or systemd
4. Set up proper logging
5. Use environment variables for all config
6. Consider using PostgreSQL instead of SQLite

See `GAME_AUTO_UPDATE.md` for more details on production deployment.

## Getting Help

- Check `GAME_AUTO_UPDATE.md` for detailed documentation
- Run `python manage.py check_system_status` to diagnose issues
- Check Celery worker logs for errors
- Verify Redis is accessible: `redis-cli ping`

## Summary

**Required processes:**
```bash
# Terminal 1: Django
python manage.py runserver

# Terminal 2: Celery Worker
celery -A pickem worker --loglevel=info --pool=solo

# Terminal 3: Celery Beat
celery -A pickem beat --loglevel=info

# Terminal 4: Your commands
python manage.py check_system_status
```

**Test it works:**
```bash
curl "http://localhost:8000/api/system/status/"
python manage.py check_system_status
python manage.py poll_espn_now
```

You're all set! The system is now polling ESPN and updating games automatically. 🎉

