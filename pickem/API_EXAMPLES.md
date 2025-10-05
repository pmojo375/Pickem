# API Usage Examples

This document provides practical examples of using the game auto-update API endpoints.

## Base URL

```
http://localhost:8000/api
```

In production, replace with your actual domain.

## Authentication

Currently, all API endpoints are public and don't require authentication. Consider adding authentication for production use.

## Endpoints

### 1. List All Games

**Endpoint:** `GET /api/games/`

**Example:**
```bash
curl "http://localhost:8000/api/games/"
```

**Response:**
```json
{
  "games": [
    {
      "id": 1,
      "external_id": "401628478",
      "home_team": {
        "id": 45,
        "name": "Michigan",
        "abbreviation": "MICH",
        "nickname": "Wolverines",
        "logo_url": "https://...",
        "conference": "Big Ten",
        "primary_color": "#00274C",
        "record": "4-0"
      },
      "away_team": {
        "id": 67,
        "name": "Ohio State",
        "abbreviation": "OSU",
        "nickname": "Buckeyes",
        "logo_url": "https://...",
        "conference": "Big Ten",
        "primary_color": "#BB0000",
        "record": "3-1"
      },
      "kickoff": "2024-09-28T19:30:00Z",
      "home_score": 24,
      "away_score": 17,
      "quarter": 3,
      "clock": "8:42",
      "is_final": false,
      "spread": {
        "home": -7.5,
        "away": 7.5
      },
      "status_state": "in",
      "status_detail": "3rd Quarter",
      "broadcast_network": "ABC"
    }
  ],
  "count": 1,
  "metadata": {
    "has_live_games": true,
    "live_game_count": 8,
    "last_update": "2024-09-28T20:15:00Z"
  }
}
```

### 2. Filter by Date

**Endpoint:** `GET /api/games/?date=YYYY-MM-DD`

**Example:**
```bash
curl "http://localhost:8000/api/games/?date=2024-09-28"
```

Get all games for September 28, 2024.

### 3. Filter for Live Games Only

**Endpoint:** `GET /api/games/?live=true`

**Example:**
```bash
curl "http://localhost:8000/api/games/?live=true"
```

Returns only games currently in progress.

### 4. Combine Filters

**Endpoint:** `GET /api/games/?date=YYYY-MM-DD&live=true`

**Example:**
```bash
curl "http://localhost:8000/api/games/?date=2024-09-28&live=true"
```

Get all live games on a specific date.

### 5. Filter by Team

**Endpoint:** `GET /api/games/?team=<team_id>`

**Example:**
```bash
curl "http://localhost:8000/api/games/?team=45"
```

Get all games for a specific team (home or away).

### 6. Limit Results

**Endpoint:** `GET /api/games/?limit=<number>`

**Example:**
```bash
curl "http://localhost:8000/api/games/?limit=10"
```

Limit to 10 results (max: 500).

### 7. Get Single Game Details

**Endpoint:** `GET /api/games/<game_id>/`

**Example:**
```bash
curl "http://localhost:8000/api/games/123/"
```

**Response:**
```json
{
  "game": {
    "id": 123,
    "external_id": "401628478",
    "home_team": {...},
    "away_team": {...},
    "kickoff": "2024-09-28T19:30:00Z",
    "home_score": 31,
    "away_score": 24,
    "quarter": null,
    "clock": "",
    "is_final": true,
    "spread": {
      "home": -7.5,
      "away": 7.5
    },
    "season": {
      "year": 2024,
      "name": "2024 Season"
    },
    "status_state": "post",
    "status_detail": "Final",
    "broadcast_network": "ABC"
  }
}
```

### 8. Get All Live Games

**Endpoint:** `GET /api/games/live/`

**Example:**
```bash
curl "http://localhost:8000/api/games/live/"
```

**Note:** This endpoint is NOT cached to ensure real-time data.

**Response:**
```json
{
  "games": [
    {...},
    {...}
  ],
  "count": 8,
  "timestamp": "2024-09-28T20:15:30Z"
}
```

### 9. Get Upcoming Games

**Endpoint:** `GET /api/games/upcoming/?days=<number>`

**Example:**
```bash
curl "http://localhost:8000/api/games/upcoming/?days=3"
```

Get games in the next 3 days (default: 7, max: 30).

**Response:**
```json
{
  "games": [
    {...},
    {...}
  ],
  "count": 24,
  "date_range": {
    "start": "2024-09-28T20:15:00Z",
    "end": "2024-10-01T20:15:00Z"
  }
}
```

### 10. System Status

**Endpoint:** `GET /api/system/status/`

**Example:**
```bash
curl "http://localhost:8000/api/system/status/"
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2024-09-28T20:15:00Z",
  "circuit_breaker": {
    "is_open": false,
    "failure_count": 0,
    "last_failure_time": null,
    "can_attempt": true
  },
  "live_state": {
    "has_live_games": true,
    "live_game_count": 8,
    "last_check": "2024-09-28T20:14:30Z"
  },
  "last_poll": "2024-09-28T20:14:30",
  "active_season": {
    "year": 2024,
    "name": "2024 Season",
    "total_games": 156
  },
  "polling_intervals": {
    "live": 60,
    "normal": 300,
    "offseason": 3600
  }
}
```

## Frontend Integration Examples

### React Hook for Live Games

```javascript
import { useState, useEffect } from 'react';

function useLiveGames() {
  const [games, setGames] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function fetchGames() {
      try {
        const response = await fetch('/api/games/live/');
        if (!response.ok) throw new Error('Failed to fetch');
        const data = await response.json();
        setGames(data.games);
        setLoading(false);
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    }

    fetchGames();
    const interval = setInterval(fetchGames, 30000); // Poll every 30s

    return () => clearInterval(interval);
  }, []);

  return { games, loading, error };
}

// Usage
function LiveScores() {
  const { games, loading, error } = useLiveGames();

  if (loading) return <div>Loading...</div>;
  if (error) return <div>Error: {error}</div>;

  return (
    <div>
      <h2>Live Games ({games.length})</h2>
      {games.map(game => (
        <GameCard key={game.id} game={game} />
      ))}
    </div>
  );
}
```

### Vanilla JavaScript Polling

```javascript
class GameScorePoller {
  constructor(endpoint, callback, interval = 30000) {
    this.endpoint = endpoint;
    this.callback = callback;
    this.interval = interval;
    this.timerId = null;
  }

  async poll() {
    try {
      const response = await fetch(this.endpoint);
      const data = await response.json();
      this.callback(data);
      
      // Adjust polling interval based on live games
      const hasLive = data.metadata?.has_live_games || false;
      this.interval = hasLive ? 30000 : 300000; // 30s or 5min
      
    } catch (error) {
      console.error('Polling error:', error);
      this.callback({ error: error.message });
    }
  }

  start() {
    this.poll(); // Initial poll
    this.timerId = setInterval(() => this.poll(), this.interval);
  }

  stop() {
    if (this.timerId) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
  }
}

// Usage
const poller = new GameScorePoller(
  '/api/games/live/',
  (data) => {
    console.log('Received games:', data.games);
    updateScoreboard(data.games);
  }
);

poller.start();

// Stop when leaving page
window.addEventListener('beforeunload', () => poller.stop());
```

### jQuery Example

```javascript
function fetchGames(filters = {}) {
  const params = new URLSearchParams(filters);
  
  $.ajax({
    url: `/api/games/?${params}`,
    method: 'GET',
    dataType: 'json',
    success: function(data) {
      renderGames(data.games);
      
      // Schedule next poll if games are live
      if (data.metadata.has_live_games) {
        setTimeout(() => fetchGames(filters), 30000);
      }
    },
    error: function(xhr, status, error) {
      console.error('Failed to fetch games:', error);
    }
  });
}

// Fetch live games on September 28
fetchGames({ date: '2024-09-28', live: 'true' });
```

### Python Client

```python
import requests
from typing import Dict, List, Optional

class PickemAPIClient:
    def __init__(self, base_url: str = "http://localhost:8000/api"):
        self.base_url = base_url
        self.session = requests.Session()
    
    def get_games(
        self,
        date: Optional[str] = None,
        live: bool = False,
        team: Optional[int] = None,
        limit: int = 100
    ) -> Dict:
        """Get games with optional filters."""
        params = {'limit': limit}
        if date:
            params['date'] = date
        if live:
            params['live'] = 'true'
        if team:
            params['team'] = team
        
        response = self.session.get(f"{self.base_url}/games/", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_live_games(self) -> List[Dict]:
        """Get all currently live games."""
        response = self.session.get(f"{self.base_url}/games/live/")
        response.raise_for_status()
        return response.json()['games']
    
    def get_game(self, game_id: int) -> Dict:
        """Get a specific game by ID."""
        response = self.session.get(f"{self.base_url}/games/{game_id}/")
        response.raise_for_status()
        return response.json()['game']
    
    def get_system_status(self) -> Dict:
        """Get system status."""
        response = self.session.get(f"{self.base_url}/system/status/")
        response.raise_for_status()
        return response.json()

# Usage
client = PickemAPIClient()

# Get today's live games
live_games = client.get_live_games()
for game in live_games:
    print(f"{game['away_team']['name']} @ {game['home_team']['name']}: "
          f"{game['away_score']}-{game['home_score']}")

# Check system health
status = client.get_system_status()
print(f"System status: {status['status']}")
print(f"Circuit breaker: {'OPEN' if status['circuit_breaker']['is_open'] else 'CLOSED'}")
```

## Error Handling

All endpoints return appropriate HTTP status codes:

- `200 OK`: Success
- `400 Bad Request`: Invalid parameters
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error

**Error Response Format:**
```json
{
  "error": "Invalid date format. Use YYYY-MM-DD"
}
```

**Example Error Handling:**
```javascript
async function fetchGamesWithErrorHandling(date) {
  try {
    const response = await fetch(`/api/games/?date=${date}`);
    
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || 'Unknown error');
    }
    
    return await response.json();
  } catch (error) {
    console.error('API Error:', error.message);
    // Show user-friendly error message
    return { games: [], error: error.message };
  }
}
```

## Rate Limiting

While not currently enforced, best practices for API usage:

1. **Cache responses** when appropriate
2. **Poll no more frequently than once per 30 seconds** for live games
3. **Use appropriate endpoints**: `/api/games/live/` instead of filtering all games
4. **Respect HTTP cache headers** when they're added
5. **Implement exponential backoff** on errors

## Testing

### Using curl with pretty output

```bash
# Install jq for JSON formatting
# Mac: brew install jq
# Linux: apt-get install jq
# Windows: choco install jq

curl -s "http://localhost:8000/api/games/live/" | jq
```

### Using httpie (better than curl)

```bash
# Install: pip install httpie

http GET localhost:8000/api/games/ date==2024-09-28 live==true
```

### Using Postman

1. Create a new collection called "Pickem API"
2. Add requests for each endpoint
3. Use environment variables for base URL
4. Set up tests to verify response structure

## Next Steps

- Integrate these endpoints into your frontend
- Set up proper error handling and retry logic
- Implement caching on the client side
- Consider WebSocket support for real-time updates (future enhancement)

