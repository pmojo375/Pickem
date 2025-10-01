# Spread Display Guide

## How Spreads Are Shown

Spreads are displayed from the **home team's perspective** with the spread value indicating if they're favored or underdog.

### Format
The spread value appears directly after the home team name:
```
Michigan -7      (home team favored by 7 points)
Michigan +10     (home team underdog by 10 points)
Michigan -3.5    (home team favored by 3.5 points)
Michigan PK      (pick'em - even game)
Michigan         (no spread available yet - just team name shown)
```

### Examples
- **Michigan -7** → Michigan (home) is favored by 7 points
- **Ohio State +10** → Ohio State (home) is underdog by 10 points
- **Alabama -3.5** → Alabama (home) is favored by 3.5 points
- **Texas PK** → Even matchup (pick'em game)
- **Michigan** → Spread not available yet

### Reading the Spread
- **Negative (-)** = Home team is favored
- **Positive (+)** = Home team is underdog
- **PK** = Pick'em (even game, no favorite)

## Where Spreads Are Displayed

### 1. **Settings Page**
- Spread shown directly after home team name in bold
- Format: `Michigan -7`
- Shows **current spread** (updates when you click "Update Spreads" button)

### 2. **Picks Page**
- Spread shown directly after home team name in bold
- Format: `Ohio State at Michigan -7`
- Shows **locked pick spread** (frozen when game was selected)
- Helps you make informed picks

### 3. **Live Page**
- Spread shown directly after home team name in bold
- Format: `Ohio State at Michigan -7`
- Shows **locked pick spread** at time of picking

## Template Tag

The spread display uses the `game_spread` template tag:

```django
{% load cfb_tags %}
{% game_spread game %}
```

### Logic
1. Returns just the **spread value** (not the team name)
2. **Negative (-)** = home team favored
3. **Positive (+)** = home team underdog
4. **Zero (0)** → returns "PK" (pick'em)
5. No spread data → returns empty string (no spread shown)

## Why This Works

✅ **Consistent** - Always references the home team  
✅ **Clear** - Easy to understand who's favored at a glance  
✅ **Concise** - Shows spread once (from home team perspective)  
✅ **Standard** - Matches how sportsbooks display spreads  
✅ **Simple** - No confusion about which team the spread applies to

