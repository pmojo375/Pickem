# Timezone Configuration - Eastern Time

## Changes Made

### 1. Settings Updated
Changed `TIME_ZONE` from `'UTC'` to `'America/New_York'` in `settings.py`:
```python
TIME_ZONE = 'America/New_York'  # Eastern Time
USE_TZ = True  # Keep timezone-aware datetimes
```

**How it works:**
- Database stores all times in UTC (standard practice)
- Django automatically converts to Eastern when displaying
- Handles DST (Daylight Saving Time) automatically

### 2. Template Filter Added
Created `eastern_time` filter in `cfb_tags.py`:
```django
{{ game.kickoff|eastern_time }}
```

**Output format:** `Mon, Oct 1 at 3:30 PM ET`

**Pages updated:**
- ✅ Picks page
- ✅ Live page

### 3. Game Admin Enhanced
Added spread display to the admin interface:
- **List view:** Shows "Current Spread" column
- **Detail view:** Shows both "Opening Spread" and "Current Spread" as readonly fields
- **Format:** `Home: +3.5 / Away: -3.5`

### 4. Verification Commands

**Check if all times are timezone-aware:**
```bash
cd pickem
python manage.py verify_timezones
```

**Fix any naive datetimes (if found):**
```bash
# Dry run first to see what would change
python manage.py fix_naive_times --dry-run

# Actually apply the fixes
python manage.py fix_naive_times
```

## No Data Migration Needed! ✓

Since `USE_TZ = True` was already set, all your existing data is **already timezone-aware** (stored as UTC). The change to `TIME_ZONE = 'America/New_York'` only affects how times are **displayed**, not how they're stored.

## What You'll See

**Before:** `2025-10-01 19:30:00+00:00` (UTC)  
**After:** `Wed, Oct 1 at 3:30 PM ET` (Eastern, automatically converted)

The time displayed will be correct for Eastern timezone, including automatic DST handling!

