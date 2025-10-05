# Periodic Tasks - Quick Summary

## ✅ What Was Implemented

Your request to add periodic tasks for game polling and spread updates has been completed!

## 📋 New Tasks Added

### 1. **Game Polling** → `sync_upcoming_games`
- **When**: Once per week on **Monday at 6:00 AM**
- **What**: Syncs upcoming games from CFBD API (ESPN fallback)
- **Result**: New games automatically added to database weekly

### 2. **Daily Spread Update** → `update_spreads`
- **When**: Daily at 9:00 AM
- **What**: Fetches betting spreads from The Odds API
- **Result**: Spreads updated once per day

### 3. **Post-Completion Spread Update** → `check_and_update_spreads_on_completion`
- **When**: Every hour (checks for completion)
- **What**: Detects when all weekly games are done, then updates spreads one final time
- **Result**: Final spread snapshot after games complete

## 🔧 Files Modified

1. **`pickem/cfb/tasks.py`**
   - Added `sync_upcoming_games()` task
   - Added `update_spreads()` task
   - Added `check_and_update_spreads_on_completion()` task

2. **`pickem/pickem/celery.py`**
   - Added 3 new scheduled tasks to `beat_schedule`
   - Added task routing for new tasks

## 🚀 How to Use

### Start Celery Beat (if not running)
```bash
cd pickem
.\start_beat.bat  # Windows
# or
./start_beat.sh   # Linux/Mac
```

### Manually Trigger Tasks (for testing)
```python
# Sync games immediately
from cfb.tasks import sync_upcoming_games
sync_upcoming_games.delay()

# Update spreads immediately
from cfb.tasks import update_spreads
update_spreads.delay()

# Check game completion
from cfb.tasks import check_and_update_spreads_on_completion
check_and_update_spreads_on_completion.delay()
```

## ⚙️ Configuration Required

### Game Polling (CFBD preferred)
```bash
CFBD_API_KEY=your_key_here  # Recommended
# Falls back to ESPN if not set
```

### Spread Updates (Required)
```bash
ODDS_API_KEY=your_key_here  # Required for spreads
```

## 📊 Task Schedule

```
Monday 6:00 AM → Sync games (CFBD/ESPN) - Weekly
Daily 9:00 AM  → Update spreads (daily)
Every Hour     → Check if games done
                  └─ If all final: Update spreads (closing lines)
```

## 🎯 Expected Behavior

1. **Every Monday at 6 AM**: New games for the week are pulled from CFBD (weekly)
2. **Every morning at 9 AM**: Spreads are updated for all upcoming games (daily)
3. **After all games complete**: Closing spreads captured automatically
4. **During games**: Live scores continue updating every minute (existing behavior)

## ✨ Key Features

- ✅ Automatic game synchronization
- ✅ Daily spread updates
- ✅ Post-completion spread capture
- ✅ CFBD API with ESPN fallback
- ✅ Rate limiting and error handling
- ✅ Prevents duplicate updates
- ✅ Redis caching for efficiency

## 📝 Notes

- Game sync uses existing `fetch_and_store_week()` which prefers CFBD over ESPN
- Spread updates only happen if spreads change by >= 0.5 points
- Post-completion update only runs once per week (cached)
- All tasks route to the 'scores' queue
- Tasks expire if not executed within their timeout window

## 📚 Full Documentation

See [PERIODIC_TASKS_SETUP.md](PERIODIC_TASKS_SETUP.md) for complete documentation including:
- Detailed task descriptions
- Monitoring commands
- Troubleshooting guide
- Customization options

## ✅ Verification

After starting Celery Beat, verify tasks are scheduled:

```bash
celery -A pickem inspect scheduled
```

You should see your new periodic tasks listed!

