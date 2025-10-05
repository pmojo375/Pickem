# Timezone Handling Notes

## Issue Discovered: October 2024

### The Problem

When implementing live score updates, we discovered a timezone mismatch:

**Scenario:**
- User's local time: October 4, 2024 (10:00 PM ET)
- Server/UTC time: October 5, 2024 (2:00 AM UTC)
- Games scheduled: October 4, 2024

**What happened:**
1. JavaScript used `new Date().toISOString()` which returns UTC date (Oct 5)
2. API searched for games on Oct 5
3. No games found! (All games are on Oct 4)
4. Live updates appeared "stuck"

### The Solution

**Remove date filtering from frontend polling:**

```javascript
// ❌ OLD (buggy):
const today = new Date().toISOString().split('T')[0];  // UTC date!
const url = `/api/games/?date=${today}`;

// ✅ NEW (works):
const url = `/api/games/?limit=100`;  // No date filter
```

**Why this works:**
- API defaults to showing recent games anyway (last 2-3 days)
- Avoids all timezone conversion issues
- Catches late-night games that may span date boundaries
- Works for users in any timezone

### Alternative Solutions (Not Used)

**Option 1: Use local date properly**
```javascript
const now = new Date();
const localDate = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
```
- **Pro:** More precise
- **Con:** Still has issues with late-night games (11:30 PM game might not show)

**Option 2: Query multiple dates**
```javascript
const url = `/api/games/?date=${yesterday}&date=${today}`;
```
- **Pro:** Catches games on both days
- **Con:** API doesn't support multiple date params (would need modification)

**Option 3: Add timezone parameter to API**
```javascript
const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const url = `/api/games/?date=${today}&tz=${timezone}`;
```
- **Pro:** Server handles timezone conversion
- **Con:** Requires API changes, more complex

### Chosen Approach

**No date filter = simplest and most reliable**

The API's default behavior (showing games from the check window) works perfectly:
- Backend (Celery) checks games from last 2 days forward
- API returns these games by default
- Frontend displays whatever the API returns
- Works globally, no timezone issues!

### Django Timezone Settings

```python
# settings.py
TIME_ZONE = 'America/New_York'  # Eastern Time
USE_TZ = True  # Use timezone-aware datetimes
```

**Important:** All datetime values in the database are stored as UTC, but displayed/interpreted in Eastern Time.

### API Behavior

```python
# Default date range (when no date parameter)
now = timezone.now()  # UTC-aware
start_date = now - timedelta(days=GAME_CHECK_WINDOW_PAST)  # 2 days ago
end_date = now + timedelta(days=GAME_CHECK_WINDOW_FUTURE)  # 1 day forward

games = Game.objects.filter(
    season=active_season,
    kickoff__gte=start_date,
    kickoff__lte=end_date
)
```

This gives us a 3-day window centered on "now", which catches all relevant games regardless of timezone.

### Browser Timezone Detection

The JavaScript now logs both values for debugging:

```javascript
console.log(`Polling (Local: ${localDate}, UTC: ${utcDate})`);
```

**Example output:**
```
Polling /api/games/?limit=100 (Local date: 2024-10-04, UTC would be: 2024-10-05)
```

This helps identify timezone issues in production.

### Testing Across Timezones

**To test in different timezones:**

1. **Change browser timezone** (Chrome DevTools):
   - F12 → Console
   - Settings (gear icon) → Preferences
   - Search for "timezone"
   - Set to different timezone
   - Hard refresh page

2. **Change system timezone:**
   - Windows: Settings → Time & Language → Date & Time
   - Mac: System Preferences → Date & Time
   - Linux: `timedatectl set-timezone America/Los_Angeles`

3. **Test dates:**
   - Late night (11 PM - 2 AM) - Games might span dates
   - Daylight Saving Time transitions
   - International timezones (Europe, Asia, Australia)

### Lessons Learned

1. **Always use timezone-aware datetimes** in the database
2. **Avoid date conversions in JavaScript** - use server's date logic
3. **Log both local and UTC dates** for debugging
4. **Test with users in different timezones**
5. **Late-night games** (11:30 PM starts) are edge cases
6. **Don't trust `.toISOString()`** for local dates - it always returns UTC!

### Related Files

- `static/js/live-score-updater.js` - Frontend polling logic
- `cfb/api_views.py` - API endpoint (date filtering)
- `pickem/settings.py` - Timezone configuration
- `TIMEZONE_SETUP.md` - Original timezone documentation

### Quick Reference

**Get local date (NOT UTC):**
```javascript
const now = new Date();
const localDate = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
```

**Get UTC date:**
```javascript
const utcDate = new Date().toISOString().split('T')[0];
```

**Get timezone offset:**
```javascript
const offset = new Date().getTimezoneOffset();  // Minutes from UTC
const hours = -offset / 60;  // Hours from UTC (negative = west of UTC)
```

**Get timezone name:**
```javascript
const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;  // e.g., "America/New_York"
```

---

**Bottom line:** When in doubt, don't filter by date on the frontend. Let the backend handle the date logic!

