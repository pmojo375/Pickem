# System Architecture - Game Auto-Update

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Frontend/Client Layer                        │
│  (React, Vue, Vanilla JS - Polls API every 30-300 seconds)         │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ HTTP GET /api/games/*
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        Django Application Layer                      │
│                                                                       │
│  ┌─────────────────┐    ┌──────────────────┐   ┌─────────────────┐│
│  │   API Views     │    │   Web Views      │   │  Management     ││
│  │ (api_views.py)  │    │  (views.py)      │   │  Commands       ││
│  │                 │    │                  │   │                 ││
│  │ • /api/games/   │    │ • /live/         │   │ • poll_espn_now ││
│  │ • /api/status/  │    │ • /picks/        │   │ • sync_games    ││
│  └────────┬────────┘    └──────────────────┘   └─────────────────┘│
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────────────────────────────────────────┐          │
│  │              Database Layer (ORM)                     │          │
│  │  Models: Game, Team, Season, Pick, League, etc.      │          │
│  └────────┬─────────────────────────────────────────────┘          │
└───────────┼──────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          SQLite/PostgreSQL                           │
│                        (Persistent Storage)                          │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         Celery Worker Layer                          │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │                        Celery Tasks                             ││
│  │                                                                  ││
│  │  • poll_espn_scores         (Main polling task)                ││
│  │  • adjust_polling_interval  (Dynamic interval adjustment)      ││
│  │  • update_single_game       (Single game update)               ││
│  │  • sync_games_from_espn     (Bulk sync)                        ││
│  │  • cleanup_old_game_cache   (Cache maintenance)                ││
│  └────────┬─────────────────────────────────────────────────────────┘│
│           │                                                          │
│           ▼                                                          │
│  ┌──────────────────────────────────────────────────────┐          │
│  │           ESPN API Client (espn_api.py)              │          │
│  │                                                       │          │
│  │  • Circuit Breaker Pattern                           │          │
│  │  • Retry with Exponential Backoff                    │          │
│  │  • Rate Limiting & Jitter                            │          │
│  │  • Error Handling                                    │          │
│  └────────┬─────────────────────────────────────────────┘          │
└───────────┼──────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           ESPN API                                   │
│   https://site.api.espn.com/apis/site/v2/sports/football/...       │
│                    (Unofficial Public API)                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       Redis Cache Layer                              │
│                                                                       │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │  Celery Queue (DB 0)          Cache Data (DB 1)                ││
│  │  ─────────────────────        ────────────────                 ││
│  │  • Task messages              • scores:game:{id}               ││
│  │  • Task results               • scores:live_state              ││
│  │  • Beat schedule              • scores:circuit_breaker         ││
│  │                               • scores:last_poll               ││
│  └────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       Celery Beat Scheduler                          │
│                                                                       │
│  Every 60s  → poll_espn_scores                                      │
│  Every 30s  → adjust_polling_interval                               │
│  Every 15m  → cleanup_old_game_cache                                │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Polling Flow (Server → ESPN → Database)

```
┌──────────────┐
│ Celery Beat  │
│  Scheduler   │
└──────┬───────┘
       │ Triggers every 60s
       ▼
┌────────────────────┐
│ poll_espn_scores   │
│      Task          │
└──────┬─────────────┘
       │ 1. Check last poll time (Redis)
       │ 2. Skip if < 30s since last poll
       ▼
┌────────────────────┐
│  ESPN API Client   │
│ (Circuit Breaker)  │
└──────┬─────────────┘
       │ 3. Fetch games for date range
       │ 4. Retry on failure (3x)
       │ 5. Open circuit if 5 failures
       ▼
┌────────────────────┐
│  Parse & Validate  │
│   ESPN Response    │
└──────┬─────────────┘
       │ 6. Parse each game
       │ 7. Normalize data
       ▼
┌────────────────────┐
│  Change Detection  │
│                    │
└──────┬─────────────┘
       │ 8. Compare with DB
       │ 9. Only update if changed
       ▼
┌────────────────────┐
│  Update Database   │
│   & Redis Cache    │
└──────┬─────────────┘
       │ 10. Save to DB
       │ 11. Update Redis cache
       │ 12. Grade picks if final
       ▼
┌────────────────────┐
│  Update Live State │
│    (Redis)         │
└────────────────────┘
     13. Set has_live_games flag
     14. Store live game count
```

### 2. API Request Flow (Client → Django → Cache/DB)

```
┌─────────────┐
│   Client    │
│  (Browser)  │
└──────┬──────┘
       │ GET /api/games/live/
       ▼
┌──────────────────┐
│  Django API View │
│  (api_views.py)  │
└──────┬───────────┘
       │ 1. Parse query params
       │ 2. Check HTTP cache
       ▼
┌──────────────────┐
│  Query Database  │
│  (with filters)  │
└──────┬───────────┘
       │ 3. Filter by date/live/team
       │ 4. Select related (teams, season)
       ▼
┌──────────────────┐
│  Enhance with    │
│  Redis Cache     │
└──────┬───────────┘
       │ 5. Get cached ESPN data
       │ 6. Add status_state, broadcast
       ▼
┌──────────────────┐
│  Serialize &     │
│  Return JSON     │
└──────┬───────────┘
       │ 7. Format as JSON
       │ 8. Add metadata (live state)
       ▼
┌──────────────────┐
│    Response      │
│   (with Cache    │
│    headers)      │
└──────────────────┘
```

### 3. Circuit Breaker State Machine

```
         ┌──────────────┐
    ┌───▶│    CLOSED    │◀──┐
    │    │  (Normal)    │   │
    │    └──────┬───────┘   │
    │           │            │ Success
    │      5 failures        │
    │           ▼            │
    │    ┌──────────────┐   │
    │    │     OPEN     │   │
    │    │  (Blocking)  │   │
    │    └──────┬───────┘   │
    │           │            │
    │      5 minutes         │
    │           ▼            │
    │    ┌──────────────┐   │
    └────│  HALF-OPEN   │───┘
         │ (Testing)    │
         └──────────────┘
              │
         Failure ↓
         (back to OPEN)
```

## Component Interactions

### ESPN API Client Workflow

```
Request
   │
   ▼
┌─────────────────┐
│ Circuit Breaker │  ◀── Checks if circuit is OPEN
│   Check         │      If OPEN → Return None
└────────┬────────┘
         │ Circuit CLOSED
         ▼
┌─────────────────┐
│  Rate Limiter   │  ◀── Wait if too many recent requests
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  HTTP Request   │
│  with Timeout   │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
Success     Failure
    │         │
    ▼         ▼
┌────────┐ ┌──────────────┐
│ Parse  │ │   Retry      │
│ JSON   │ │   Logic      │
└───┬────┘ └──────┬───────┘
    │             │
    │         ┌───┴────┐
    │         │        │
    │     Success   3 Failures
    │         │        │
    ▼         ▼        ▼
┌─────────────────────────┐
│  Update Circuit Breaker │
│  • Success → Reset      │
│  • Failure → Increment  │
└─────────────────────────┘
```

## Polling Strategy

### Dynamic Interval Adjustment

```
┌──────────────────────────────────────────┐
│     Check Live State in Redis           │
└──────────────┬───────────────────────────┘
               │
        ┌──────┴──────┐
        │             │
   Live Games?   No Live Games
        │             │
        ▼             ▼
┌──────────────┐  ┌─────────────┐
│ Poll every   │  │ Poll every  │
│  60 seconds  │  │ 300 seconds │
└──────────────┘  └─────────────┘

Offseason:
┌──────────────┐
│ Poll every   │
│ 3600 seconds │
└──────────────┘
```

### Last Poll Protection

```
Task Triggered
      │
      ▼
┌─────────────────┐
│ Check Redis for │
│  last_poll time │
└────────┬────────┘
         │
    ┌────┴─────┐
    │          │
< 30s       ≥ 30s
    │          │
    ▼          ▼
  Skip       Poll
               │
               ▼
        ┌──────────────┐
        │ Set last_poll│
        │  timestamp   │
        └──────────────┘
```

## Cache Strategy

### Redis Key TTLs

```
scores:game:{id}           TTL: 120s (2 minutes)
scores:live_state          TTL: 180s (3 minutes)
scores:circuit_breaker     TTL: 600s (10 minutes)
scores:last_poll           TTL: 300s (5 minutes)
```

### Cache Update Flow

```
Game Updated in DB
       │
       ▼
┌──────────────────┐
│ Build Cache Data │
│  • Game info     │
│  • Team details  │
│  • Live status   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Write to Redis  │
│ Key: scores:game │
│     :{external_id}│
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Set TTL: 120s   │
└──────────────────┘
```

## Process Communication

```
┌──────────────┐       ┌──────────────┐
│   Django     │       │   Celery     │
│   Server     │       │   Worker     │
└──────┬───────┘       └──────┬───────┘
       │                      │
       │  ┌────────────────┐  │
       └─▶│     Redis      │◀─┘
          │   (Message     │
          │    Broker)     │
          └────────┬───────┘
                   │
          ┌────────┴───────┐
          │                │
    ┌─────▼─────┐   ┌─────▼─────┐
    │  Celery   │   │   Cache   │
    │  Tasks    │   │   Data    │
    │  (DB 0)   │   │   (DB 1)  │
    └───────────┘   └───────────┘
```

## Error Handling Flow

```
ESPN API Call
      │
  ┌───┴────┐
  │        │
Success  Failure
  │        │
  │        ▼
  │   ┌──────────────┐
  │   │   Retry      │
  │   │  (up to 3x)  │
  │   └───┬──────────┘
  │       │
  │   ┌───┴────┐
  │   │        │
  │ Success  All Failed
  │   │        │
  │   ▼        ▼
  │ ┌────────────────┐
  │ │ Record Failure │
  │ │ in Circuit     │
  │ │ Breaker        │
  │ └───┬────────────┘
  │     │
  │     ▼
  │ ┌────────────────┐
  │ │ 5+ Failures?   │
  │ └───┬────────────┘
  │     │
  │ ┌───┴────┐
  │ │        │
  │No      Yes
  │ │        │
  ▼ ▼        ▼
┌─────────┐ ┌──────────┐
│Continue │ │  OPEN    │
│Polling  │ │ Circuit  │
└─────────┘ └──────────┘
```

## Deployment Architecture

### Development

```
┌─────────────────────────────────────┐
│         Single Server               │
│  ┌──────────────────────────────┐  │
│  │  Django (runserver)          │  │
│  │  Celery Worker (1 process)   │  │
│  │  Celery Beat (1 process)     │  │
│  │  Redis (Docker or local)     │  │
│  │  SQLite                      │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

### Production

```
┌─────────────────────────────────────────────┐
│            Load Balancer (Nginx)            │
└───────┬─────────────────────────────┬───────┘
        │                             │
┌───────▼──────┐            ┌─────────▼───────┐
│  Django App  │            │  Django App     │
│  Server 1    │            │  Server 2       │
│  (Gunicorn)  │            │  (Gunicorn)     │
└───────┬──────┘            └─────────┬───────┘
        │                             │
        └──────────┬──────────────────┘
                   │
        ┌──────────▼──────────┐
        │   PostgreSQL DB     │
        │   (with replicas)   │
        └──────────┬──────────┘
                   │
┌──────────────────┴──────────────────┐
│                                     │
│  ┌────────────┐    ┌─────────────┐ │
│  │   Celery   │    │   Celery    │ │
│  │  Worker 1  │    │  Worker 2   │ │
│  └────────────┘    └─────────────┘ │
│                                     │
│  ┌────────────┐                    │
│  │   Celery   │                    │
│  │    Beat    │                    │
│  └────────────┘                    │
└──────────┬──────────────────────────┘
           │
    ┌──────▼──────┐
    │    Redis    │
    │  (Cluster)  │
    └─────────────┘
```

## Security Considerations

```
┌─────────────────────────────────────┐
│         Security Layers             │
├─────────────────────────────────────┤
│ 1. HTTPS/TLS for all external API  │
│ 2. API rate limiting per client    │
│ 3. CORS configuration               │
│ 4. Redis authentication             │
│ 5. Environment variable secrets     │
│ 6. No ESPN API key exposure         │
│ 7. Input validation on all params  │
│ 8. SQL injection protection (ORM)  │
└─────────────────────────────────────┘
```

## Monitoring Points

```
┌──────────────────────────────────────────┐
│         What to Monitor                  │
├──────────────────────────────────────────┤
│ • Circuit breaker state (alert if OPEN) │
│ • Last poll timestamp                    │
│ • API error rate                         │
│ • Worker process health                  │
│ • Redis connectivity                     │
│ • Task queue length                      │
│ • Database connection pool               │
│ • Response times                         │
└──────────────────────────────────────────┘
```

---

This architecture is designed to be:
- **Reliable**: Circuit breaker, retries, error handling
- **Scalable**: Horizontal scaling of workers and web servers
- **Maintainable**: Clear separation of concerns
- **Observable**: Multiple monitoring points
- **Efficient**: Caching, change detection, minimal writes

