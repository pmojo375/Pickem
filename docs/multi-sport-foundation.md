# Multi-sport data and pick'em

The application now uses a shared sport/season/period model for football and basketball. Men's college basketball has a CBBD adapter using the existing `CFBD_API_KEY`. No extra key is required. The `cfb` Django app label remains stable so existing migrations, permissions, and deployments retain their history.

## Models

- `Sport` selects a registered data provider and configures the ESPN scoreboard path, period labels, and football-specific live details. Football uses `cfbd`; men's college basketball uses `cbbd`. Other sports can be configured without changing the shared schema; a new data provider needs an adapter registered in `cfb/services/providers/`.
- `Season` belongs to one sport. The database enforces one active season per sport and uniqueness of `(sport, year)`.
- `SchedulePeriod` replaces `Week`. Its kinds are `week`, `round`, `matchday`, `day`, and `slate`. Each period has a season, number, dates, and optional display name. Existing football weeks keep their IDs and become periods of kind `week`.
- `MemberPeriod` replaces `MemberWeek`. Game/ranking/spread/statistics relationships and pick limits, dropped periods, and payouts use the neutral model. `Game.start_time` and `Game.period` replace `kickoff` and `quarter`. Public game JSON retains the old two keys as compatibility aliases.
- Teams, games, rankings, and statistics inherit sport through their season. Picks, memberships, announcements, payments, and league rules also connect through their league. Shared locations and user accounts remain independent of sport. Admin lists and filters show these inherited sports.
- Model saves, bulk creates/updates, and relationship updates validate season/sport consistency. Invalid picked teams, cross-season rankings/statistics, cross-sport standings/payments, and invalid period dates are rejected. Normal SQL scoring updates retain their existing behavior. Raw SQL still requires correct relationships.
- Team and game `provider_ids` contain string IDs keyed by provider. CBBD IDs never enter the CFBD fields. When CBBD supplies `sourceId`, it is kept as the ESPN reference; the internal CBBD ID remains separate.

Basketball games can store tournament names and home/away seeds. An import assigns games to existing rounds/slates whose dates cover them; otherwise it creates daily periods. A later import can move those daily games into an explicitly configured round. Football retains its existing provider-calendar grouping. Other sports use explicit period membership to avoid mixing overlapping slates.

The existing pick submission, straight-up/ATS grading, standings, what-if, reminders, spread locking, and payouts operate on the selected sport's periods. Reminder and spread-lock processing covers all active sports. ESPN score polling separates feeds and identifiers by sport. Basketball never receives football down/distance information. Team logos support remote provider URLs as well as existing local football assets.

## Basketball import

Create the basketball season in Django admin (or the staff season page) before importing. CBBD uses the **ending year**: March 2027 belongs to season `2027`, representing 2026–27.

From the directory containing `manage.py`, preview an NCAA tournament import:

```sh
python manage.py sync_sport_data \
  --sport mens-college-basketball --season 2027 \
  --start-date 2027-03-01 --end-date 2027-04-10 \
  --season-type postseason --tournament NCAA \
  --include-lines --dry-run
```

`--dry-run` makes **zero network calls and zero database changes**. Remove that flag to execute. For this example an uncached run uses at most three requests: teams, games, and lines. The example dates are a bounded import window, not an asserted tournament schedule.

Options:

| Option | Effect |
| --- | --- |
| `--teams-only` | Import team metadata; dates are unnecessary |
| `--games-only` | Skip the teams endpoint; resolve/create participating teams by provider ID |
| `--include-lines` | Fetch opening/current spreads and totals in one extra request |
| `--max-requests N` | Hard per-run request ceiling, default 3, permitted range 1–20 |
| `--force` | Refresh cached responses while retaining the ceiling and rate-limit cooldown |
| `--tournament NCAA` | Request/filter the named tournament |

Imports upsert existing records rather than duplicating them and preserve other providers' IDs. Game snapshots never replace newer ESPN in-play scores with scheduled-game scores. Final results grade picks through the shared scoring signals and refresh basketball W/L and ATS records. Repeated identical spreads do not create duplicate history snapshots. Bounded tournament imports do not mark the entire season's schedule as completely imported.

The staff-only POST endpoint `/cfb-admin/import-schedule/` also accepts `sport`, `start_date`, `end_date`, optional `season_type`, `tournament`, and `include_lines=on`. Basketball requires an explicit date range and uses a three-request ceiling. Football continues through its existing initializer/import tasks under `cfb/services/providers/football.py`.

## Quota protection

CBBD requests are cached (teams for 24 hours; games and lines for one hour), including empty responses. The request budget is checked before every network request. Rate limiting stops the operation without retries and starts a cooldown shared with the football transport. Authentication failures also stop immediately. The 3,000-result provider cap causes an explicit error requesting a narrower date range; it never silently records a truncated schedule as complete.

Requested payloads are fetched before any writes. Invalid payloads roll back the import's database changes. Failed requests still consume provider quota, so cached data and dry-run planning should be preferred to repeated forced pulls.

Basketball has **no new automatic paid polling schedule**. Imports are explicit. Live scores use the existing separate ESPN flow. Existing football refresh schedules remain in place.

## API and extending sports

- `/api/sports/` exposes configured active sports and their active seasons.
- `/api/games/?sport=mens-college-basketball&season=2027` selects a sport and year.
- `/api/games/live/?sport=mens-college-basketball` and `/api/games/upcoming/?sport=mens-college-basketball` select its active season.
- Ordinary calls without a sport retain the football default. Rendered-game polling can request internal IDs directly across active seasons.

To add another provider, register its transport/normalization adapter, store provider IDs separately, and write into the selected sport's season. Credentials belong in environment variables. Other sports do not automatically inherit the basketball or football API schema. A full advance-filled bracket and bracket advancement are separate features; this is the shared game-by-game pick'em system.

## Migrations and verification

Apply migrations yourself when deploying. `0034` establishes sport ownership; `0035` renames models/fields while preserving IDs and data; `0036` adds provider configuration, seeds, period kinds, and the active-season constraint; `0037` updates payout labels. When legacy data contains multiple active seasons for a sport, the newest remains active, matching the previous selection behavior. Deploy web and Celery workers using the matching code/schema together.

Verified locally with the Django suite, mocked provider responses, migration consistency checks, a populated `0034` → latest upgrade preserving IDs/picks/statistics/rules/payouts, and browser smoke checks for live/what-if pages. Local database checks use SQLite; the repository CI workflow targets PostgreSQL. No authenticated football or basketball API requests were made during development.

Provider contracts: [CBBD games](https://api.collegebasketballdata.com/api/games), [teams](https://api.collegebasketballdata.com/api/teams), [lines](https://api.collegebasketballdata.com/api/lines), and the [official Python SDK models](https://github.com/CFBD/cbbd-python/tree/main/docs).
