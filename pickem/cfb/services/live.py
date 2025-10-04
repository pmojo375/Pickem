"""Live score utilities backed by ESPN's public scoreboard feed."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from django.db import transaction

from ..models import Game
from . import schedule


def _parse_score(raw_score: Optional[str]) -> Optional[int]:
    """Safely convert ESPN's string score values into integers."""

    if raw_score in (None, ""):
        return None
    try:
        return int(raw_score)
    except (TypeError, ValueError):
        return None


def _collect_events() -> Iterable[Dict]:
    """Fetch all scoreboard events for the current pick'em week."""

    start, end = schedule.get_week_window()
    events = schedule.fetch_weekly_games(start, end)
    # annotate so callers can re-use the window for querying locally
    for event in events:
        event["_window_start"] = start
        event["_window_end"] = end
        yield event


@transaction.atomic
def fetch_and_store_live_scores() -> int:
    """Pull live data from ESPN and update all games in the current window."""

    updated_games = 0

    for event in _collect_events():
        competitions = event.get("competitions") or []
        if not competitions:
            continue

        comp = competitions[0]
        competitors = comp.get("competitors") or []
        if len(competitors) != 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        external_id = str(event.get("id") or comp.get("id") or "")

        game: Optional[Game] = None
        if external_id:
            game = Game.objects.filter(external_id=external_id).first()

        if not game:
            # Fall back to matching by ESPN team IDs within the same week window
            home_id = str((home.get("team") or {}).get("id") or "")
            away_id = str((away.get("team") or {}).get("id") or "")
            if home_id and away_id:
                window_start = event.get("_window_start")
                window_end = event.get("_window_end")
                game = (
                    Game.objects.filter(
                        kickoff__range=(window_start, window_end),
                        home_team__espn_id=home_id,
                        away_team__espn_id=away_id,
                    )
                    .order_by("kickoff")
                    .first()
                )

        if not game:
            continue

        status: Dict = comp.get("status") or event.get("status") or {}
        status_type: Dict = status.get("type") or {}

        in_progress = status_type.get("state") == "in"
        is_final = bool(status_type.get("completed")) or status_type.get("state") == "post"
        pre_game = status_type.get("state") == "pre"

        if pre_game:
            home_score = away_score = None
        else:
            home_score = _parse_score(home.get("score"))
            away_score = _parse_score(away.get("score"))

        quarter = status.get("period") if (in_progress or is_final) else None
        clock = status.get("displayClock") if in_progress else ""

        fields_to_update = []

        def _set_field(obj: Game, attr: str, value) -> None:
            if getattr(obj, attr) != value:
                setattr(obj, attr, value)
                fields_to_update.append(attr)

        _set_field(game, "home_score", home_score)
        _set_field(game, "away_score", away_score)
        _set_field(game, "quarter", quarter if quarter not in (None, "") else None)
        _set_field(game, "clock", clock or "")
        _set_field(game, "is_final", is_final)

        if fields_to_update:
            game.save(update_fields=fields_to_update)
            updated_games += 1

    return updated_games


