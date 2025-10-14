# Against the Spread (ATS) Feature with Automatic Spread Locking

## Overview
This feature allows leagues to enable "Against the Spread" mode, which automatically locks spreads based on a configurable weekday rule. When enabled, the system will lock spreads for league games according to the specified day of the week.

## Components

### 1. League Rules Setting
- **Field**: `against_the_spread_enabled` (Boolean)
- **Location**: Settings page → League Rules section
- **Default**: True
- **Description**: Toggle to enable/disable ATS mode for a league

### 2. Spread Lock Weekday
- **Field**: `spread_lock_weekday` (Integer 0-6, Monday-Sunday)
- **Location**: Settings page → League Rules section
- **Default**: 2 (Wednesday)
- **Description**: Day of the week when spreads should be locked

### 3. Spread Locking Logic
The system uses a tiered approach to determine which spread to lock:

1. **Priority 1**: Spread from the `spread_lock_weekday` (e.g., Wednesday of game week)
2. **Priority 2**: Next available spread pulled after the lock day
3. **Priority 3**: Latest available spread (fallback)

### 4. When Spreads Are Locked

#### A. Automatic Weekly Lock (Celery Task)
- **Task**: `lock_league_spreads_for_week`
- **Trigger**: Runs automatically after `update_spreads` task completes
- **Frequency**: Daily when spreads are updated (typically 7 times per week)
- **Action**: Locks spreads for all leagues with `against_the_spread_enabled=True`

#### B. Manual Game Selection Lock
- **Trigger**: When admin selects games in Settings page
- **Condition**: Only if `against_the_spread_enabled=True` AND spread not already locked
- **Action**: Immediately locks spread using the same tiered logic
- **Note**: Admin can also use the "Lock spread when selecting games" checkbox for manual control

## Implementation Details

### Database Model
The `LeagueRules` model already included the `against_the_spread_enabled` field:
```python
against_the_spread_enabled = models.BooleanField(
    default=True,
    help_text="Allow users to pick against the spread"
)
spread_lock_weekday = models.IntegerField(
    choices=WEEKDAY_CHOICES, 
    default=2,  # Wednesday
    help_text="Day of the week when spreads lock in place if against the spread is enabled"
)
```

### Spread Storage
Spreads are stored in the `LeagueGame` model:
```python
locked_home_spread = models.DecimalField(...)
locked_away_spread = models.DecimalField(...)
spread_locked_at = models.DateTimeField(...)
```

### Historical Spread Data
The `GameSpread` model tracks all spread changes over time:
```python
class GameSpread(models.Model):
    game = models.ForeignKey(Game, ...)
    home_spread = models.DecimalField(...)
    away_spread = models.DecimalField(...)
    timestamp = models.DateTimeField(auto_now_add=True)
    source = models.CharField(...)  # e.g., bookmaker name
```

## Usage

### For League Admins
1. Navigate to Settings page
2. In "League Rules" section, check "Enable Against the Spread"
3. Select the day of week when spreads should lock (e.g., Wednesday)
4. Save League Rules
5. Select games as usual - spreads will lock automatically based on the rule

### For Developers

#### Manual Spread Lock
```python
from cfb.tasks import lock_league_spreads_for_week

# Lock spreads for current week
lock_league_spreads_for_week()

# Lock spreads for specific week
lock_league_spreads_for_week(season_year=2025, week=8, season_type='regular')
```

#### Check Locked Spreads
```python
from cfb.models import LeagueGame

# Get locked spreads for a league game
league_game = LeagueGame.objects.get(league_id=1, game_id=123)
if league_game.locked_home_spread:
    print(f"Locked spread: {league_game.locked_home_spread}")
    print(f"Locked at: {league_game.spread_locked_at}")
```

## Edge Cases Handled

1. **No spreads available**: Skips locking, logs warning
2. **Spread lock day not yet reached**: Uses latest available spread
3. **Multiple spreads on lock day**: Uses first one found
4. **Game already has locked spread**: Skips (doesn't overwrite)
5. **ATS disabled for league**: Skips that league entirely

## Logging
All spread locking operations are logged with INFO level:
- Number of spreads locked per week
- Which spread source was used (lock day, next day, or latest)
- Any errors or skipped games

## Testing

### Test Spread Locking Task
1. Ensure some games have spread data in `GameSpread` model
2. Create a league with `against_the_spread_enabled=True`
3. Add games to the league via Settings page
4. Run: `python manage.py shell`
```python
from cfb.tasks import lock_league_spreads_for_week
lock_league_spreads_for_week()
```
5. Check logs and verify spreads are locked in `LeagueGame` table

### Test Auto-Lock on Game Selection
1. Enable ATS for a league
2. Go to Settings page
3. Select games for the current week
4. Save selections
5. Verify spreads are automatically locked based on the rule

## Future Enhancements
- Add UI to view spread history for a game
- Allow manual spread lock override by admin
- Add notification when spreads are locked
- Track spread accuracy metrics

