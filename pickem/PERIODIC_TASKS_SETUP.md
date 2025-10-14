# Periodic Tasks Setup

This document describes the Celery periodic tasks configured for automatic game and spread updates.

## Overview

The system now has periodic tasks running via Celery Beat:

1. **Game Polling** - Syncs upcoming games from CFBD/ESPN
2. **Spread Updates** - Fetches betting spreads from CFBD API

## Periodic Tasks

### 1. Sync Upcoming Games (`sync_upcoming_games`)

**Task**: `cfb.tasks.sync_upcoming_games`  
**Schedule**: Weekly on Monday at 6:00 AM  
**Purpose**: Fetches and stores upcoming games for the current week

**Behavior**:
- Uses CFBD API if `CFBD_API_KEY` is configured
- Falls back to ESPN API if CFBD is unavailable
- Syncs all games in the current week window (Monday-Sunday)
- Creates or updates games in the database
- Also syncs team metadata (logos, colors, conferences, etc.)

**Configuration**:
```python
'sync-upcoming-games': {
    'task': 'cfb.tasks.sync_upcoming_games',
    'schedule': crontab(day_of_week=1, hour=6, minute=0),  # Monday at 6 AM
    'options': {'expires': 3600},
}
```

**Manual Trigger**:
```python
from cfb.tasks import sync_upcoming_games
sync_upcoming_games.delay()
```

### 2. Daily Spread Update (`update_spreads`)

**Task**: `cfb.tasks.update_spreads`  
**Schedule**: Daily at 9:00 AM  
**Purpose**: Fetches betting spreads for games in the current week

**Behavior**:
- Fetches spreads from CFBD API
- Only updates spreads that have changed (>= 0.5 point difference)
- Stores spread history in `GameSpread` table
- Updates `current_home_spread` and `current_away_spread` on `Game` model
- Sets `opening_spread` if not already set
- Requires `CFBD_API_KEY` to be configured in settings

**Configuration**:
```python
'daily-spread-update': {
    'task': 'cfb.tasks.update_spreads',
    'schedule': crontab(hour=9, minute=0),  # Daily at 9 AM
    'options': {'expires': 3600},
}
```

**Manual Trigger**:
```python
from cfb.tasks import update_spreads
update_spreads.delay()
```

### 3. ~~Check Games Completion~~ (REMOVED)

**Note**: The post-completion spread update task has been removed. Spreads captured at 9 AM daily are now considered final for games that day.

## Existing Tasks

These tasks were already configured and continue to run:

### Live Score Polling (`poll_espn_scores`)
- **Schedule**: Every 60 seconds (only when active games exist)
- Updates live scores during games
- **Optimized**: Only polls ESPN when games are active (started but not final)
- **Efficient**: Skips polling when all games are final or haven't started
- Grades picks when games complete

### Adjust Polling Interval (`adjust_polling_interval`)
- **Schedule**: Every 30 seconds
- Monitors live game state for dynamic polling

### Cleanup Old Cache (`cleanup_old_game_cache`)
- **Schedule**: Every 15 minutes
- Removes old game cache entries from Redis

## API Keys Required

### CFBD API Key (Required)
```bash
# In .env file or environment
CFBD_API_KEY=your_cfbd_api_key_here
```
- Used for: Game data, team metadata, and betting spreads
- Fallback: ESPN API (no key required) for game data only
- Get key at: https://collegefootballdata.com/key

## Starting Celery Beat

Make sure Celery Beat is running to execute these periodic tasks:

### On Windows:
```bash
cd pickem
.\start_beat.bat
```

### On Linux/Mac:
```bash
cd pickem
./start_beat.sh
```

### Manual Start:
```bash
cd pickem
celery -A pickem beat -l info
```

## Monitoring Tasks

### View Scheduled Tasks
```bash
celery -A pickem inspect scheduled
```

### View Active Tasks
```bash
celery -A pickem inspect active
```

### View Registered Tasks
```bash
celery -A pickem inspect registered
```

### Django Admin Logs
Check the Django logs for task execution:
- Task start/completion messages
- Game sync counts
- Spread update counts
- Error messages

## Task Flow

```
Weekly Timeline:
├── Monday 6:00 AM  → sync_upcoming_games
│                      └── Fetch games from CFBD/ESPN (ONCE PER WEEK)
│
Daily Timeline:
├── 9:00 AM  → update_spreads
│               └── Fetch spreads (FINAL for games that day)
│
└── Every Minute → poll_espn_scores
                   └── Update live scores
```

## Customizing Schedule

To change when tasks run, edit `pickem/pickem/celery.py`:

```python
app.conf.beat_schedule = {
    'sync-upcoming-games': {
        'schedule': crontab(day_of_week=1, hour=6, minute=0),  # Modify time here
    },
    'daily-spread-update': {
        'schedule': crontab(hour=9, minute=0),  # Modify time here
    },
    # ... other tasks
}
```

**Crontab Examples**:
- `crontab(day_of_week=1, hour=6, minute=0)` - Every Monday at 6:00 AM
- `crontab(hour=6, minute=0)` - Daily at 6:00 AM
- `crontab(hour='*/6')` - Every 6 hours
- `crontab(day_of_week=6, hour=10)` - Every Saturday at 10 AM
- `crontab(minute='*/30')` - Every 30 minutes

**Day of Week Values**: 0=Sunday, 1=Monday, 2=Tuesday, 3=Wednesday, 4=Thursday, 5=Friday, 6=Saturday

## Troubleshooting

### Tasks Not Running
1. Ensure Celery Beat is running
2. Check Redis connection
3. Verify task is registered: `celery -A pickem inspect registered`

### Spreads Not Updating
1. Verify `CFBD_API_KEY` is set
2. Check API quota/rate limits
3. Review task logs for errors

### Games Not Syncing
1. Check if `CFBD_API_KEY` is configured
2. Verify active season exists in database
3. Check ESPN API fallback is working

### Post-Completion Update Not Triggering
1. Verify all games are marked as `is_final=True`
2. Check Redis cache key: `spreads:post_completion:{week_start_date}`
3. Clear cache if needed: `cache.delete('spreads:post_completion:2024-10-05')`

## Additional Resources

- [CELERY_SETUP.md](CELERY_SETUP.md) - Initial Celery configuration
- [CELERY_COMMANDS_SUMMARY.md](CELERY_COMMANDS_SUMMARY.md) - Common Celery commands
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture overview
- [API_EXAMPLES.md](API_EXAMPLES.md) - API usage examples

