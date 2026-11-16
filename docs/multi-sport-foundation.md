# Multi-sport foundation

This change prepares storage, configuration, league/season selection, and API routing for additional sports. It does not import a basketball schedule, create tournament games, or implement bracket gameplay.

## Storage and compatibility

- `Sport` is a configurable competition namespace. The migration seeds `college-football` and `mens-college-basketball`. Add other competitions in Django admin without another schema migration (for example, women's college basketball gets its own namespace).
- `League.sport` and `Season.sport` default existing records to college football. Season years are unique **within a sport**, so football and basketball can both have a 2026 season. Teams, games, weeks, rankings, rules, picks, and standings retain their existing season/league relationships and tables.
- `Team.provider_ids` and `Game.provider_ids` store external IDs by provider, for example `{"espn": "401234567", "another_provider": "abc123"}`. Use string values. IDs are interpreted within a sport and season; do not look up external IDs globally. Existing `cfbd_id`, `espn_id`, and game `external_id` remain available. The ESPN score adapter prefers `provider_ids["espn"]` and falls back to the legacy game `external_id`.
- `Week.name` can label a round or slate such as `Sweet 16`; `display_name` falls back to `Week N`. No tournament dates or round structure are generated.
- The database field names `kickoff` and `quarter` remain for compatibility. The API additionally exposes `start_time`, `period`, `period_display`, `sport`, `season_id`, and provider IDs.

Existing football CFBD tasks and management commands explicitly select `college-football`. A new sport must use its own importer rather than calling `initialize_season` or a CFBD import task. Football record-refresh jobs are not queued for basketball finals.

## Connecting a provider

For ESPN, set these `Sport` fields in Django admin:

| Field | College football | Men's college basketball |
| --- | --- | --- |
| `espn_sport` | `football` | `basketball` |
| `espn_league` | `college-football` | `mens-college-basketball` |
| `period_label` | `Q` | `Half` |
| `regulation_periods` | `4` | `2` |
| `has_possession` | `True` | `False` |
| `has_downs` | `True` | `False` |

These values are seeded by the migration. Blank ESPN path fields disable that sport's ESPN integration. The shared adapter builds the feed URLs from configuration, polls each sport separately, and matches events within that sport. Basketball never receives football down/distance fields. Scores use separate cache namespaces, even if provider IDs overlap across sports.

For another API, add a provider adapter under `cfb/services/`, keep its credentials in environment variables, and map its responses into the existing models under the explicitly selected sport's season. Store its identifiers in `provider_ids` using a stable provider name. Avoid assigning another provider's ID to the legacy `external_id`, which the ESPN fallback interprets as an ESPN ID. Call `full_clean()` when constructing related records so cross-season teams/weeks and cross-sport league selections are rejected. Django's `save()` and bulk operations do not automatically run model validation.

Example storage after a future adapter has normalized a team payload:

```python
from cfb.models import Season, Sport, Team

sport = Sport.objects.get(pk="mens-college-basketball")
season, _ = Season.objects.get_or_create(sport=sport, year=2027)
team, _ = Team.objects.update_or_create(
    season=season,
    name="Michigan State",
    defaults={"provider_ids": {"espn": "127"}, "abbreviation": "MSU"},
)
```

When adding another provider ID to an existing team or game, merge it into `provider_ids` rather than replacing IDs from other providers. A real importer should upsert by that provider's stable ID within the selected season and handle team-name changes explicitly.

## Season and API selection

League creation includes a sport selector. Staff can start a season for a chosen sport. Rollover deactivates only that sport's previous season, copies only its rules, and pauses only its leagues. Only football starts the existing CFBD initialization automatically. Basketball season creation prepares storage and does not trigger an import.

- `GET /api/sports/` lists configured active sports and their active seasons.
- `GET /api/games/?sport=mens-college-basketball&season=2027` filters by sport and year.
- `GET /api/games/live/?sport=mens-college-basketball` and `/api/games/upcoming/?sport=mens-college-basketball` select the sport's active season.
- Existing calls without `sport` default to football. ID-based polling without `sport` can retrieve the exact rendered games across active seasons because these are internal database IDs, not provider IDs.

The common UI resolves schedules, rules, and standings through the selected league's sport. Sport-specific gameplay, additional provider imports, tournament setup, bracket advancement, and expanded statistics remain follow-up work. Existing reminder and spread-lock automation still default to football; those jobs should be generalized when enabling another sport's gameplay.

## Rollout and verification

1. Apply `python manage.py migrate` before starting the updated web/Celery processes. Migration `0034` creates and seeds sports before assigning existing leagues/seasons to football.
2. Confirm the sports in Django admin. Configure additional feeds there as needed.
3. Create basketball seasons/leagues when ready, then connect its importer in the next development step.

Verified with Django configuration checks, migration consistency checks, the full Django suite, and an upgrade from a populated pre-change schema. Local tests use SQLite and an in-memory Celery broker/backend; the existing CI workflow uses PostgreSQL and Redis.
