# Spread Locking System

## Overview

The system now supports **two types of spreads** for each game:

1. **Current Spread** - Updates whenever you fetch new odds from the API
2. **Pick Spread (Locked)** - Frozen spread used for making picks

## How It Works

### Database Fields

Added to the `Game` model:
- `pick_home_spread` - Locked home team spread for picks
- `pick_away_spread` - Locked away team spread for picks  
- `pick_spread_locked_at` - Timestamp when spread was locked

### Settings Page Workflow

1. **View Latest Spreads**
   - Settings page shows the **current spread** (latest from API)
   - Updates every time you click "Update Spreads"

2. **Enable Spread Locking (Optional)**
   - Check "Lock spreads when selecting games" checkbox at the top of the page
   - This applies to all game selections

3. **Select Games for Picks**
   - Check "Select" checkbox for each game you want to make available for picks
   - Click "Save"
   - If global lock checkbox is checked, the current spread is automatically locked

4. **What Happens When You Lock**
   - Current spread is copied to pick spread fields
   - Pick spread won't change even if current spread updates later
   - Timestamp records when spread was locked

### Picks & Live Pages

- **Always show the locked pick spread** (not current spread)
- This ensures users see the spread that was active when the game was selected
- If no locked spread exists, shows "-"

## Example Scenario

**Monday:**
- Current spread: Michigan -7.0
- You select game and check "Lock spread"
- Pick spread saved as: Michigan -7.0

**Wednesday:**
- Odds API updates, current spread changes to Michigan -8.5
- Pick spread remains: Michigan -7.0 (locked)
- Settings page shows: Michigan -8.5 (current)
- Picks page shows: Michigan -7.0 (locked)

## Future Automation

The system is designed to support automatic spread locking:

1. **Daily Schedule Lock**
   - Automatically lock spreads at a specific time each day
   - User configurable time setting
   - Runs via cron job or scheduled task

2. **Implementation Plan**
   - Add user preference for lock time (e.g., "12:00 PM daily")
   - Create management command: `python manage.py lock_game_spreads`
   - Schedule to run daily via cron/task scheduler
   - Command locks spreads for all selected games without locked spreads

## Pages & Behavior

| Page | Spread Shown | Updates When |
|------|--------------|--------------|
| **Settings** | Current | Every API fetch |
| **Picks** | Locked Pick | Only when locked |
| **Live** | Locked Pick | Only when locked |
| **Admin** | Both displayed | N/A |

## Migration Required

After this change, you need to create and run a migration:

```bash
cd pickem
python manage.py makemigrations cfb
python manage.py migrate
```

This adds the three new fields to your Game table.

