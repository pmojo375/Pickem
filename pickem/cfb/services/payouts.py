from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from cfb.models import LeagueRules

WEEKS_IN_SEASON = 12
MONEY = Decimal("0.01")


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _place_rows(structure, pool: Decimal) -> List[Dict[str, Any]]:
    if not structure:
        return []

    items = []
    for key, value in structure.items():
        try:
            place = int(key)
            percent = _as_decimal(value)
        except (TypeError, ValueError):
            continue
        if percent <= 0:
            continue
        items.append((place, percent))

    items.sort(key=lambda item: item[0])
    return [
        {
            "place": place,
            "label": f"{_ordinal(place)} place",
            "percent": percent,
            "amount": _money(pool * percent / Decimal("100")),
        }
        for place, percent in items
    ]


def build_payout_summary(league_rules: Optional[LeagueRules], member_count: int) -> Optional[Dict[str, Any]]:
    """Dollar amounts and place breakdown for a league's entry fee and payouts."""
    if not league_rules:
        return None

    entry_fee = _as_decimal(league_rules.entry_fee)
    weekly_percent = _as_decimal(league_rules.weekly_payout_percent)
    season_percent = _as_decimal(league_rules.season_payout_percent)
    last_percent = _as_decimal(league_rules.season_payout_last_percent)

    has_entry = entry_fee > 0
    has_weekly = weekly_percent > 0
    has_season = season_percent > 0
    if not has_entry and not has_weekly and not has_season:
        return None

    total_pool = _money(entry_fee * member_count)
    weekly_pool = (
        _money((total_pool * weekly_percent / Decimal("100")) / Decimal(WEEKS_IN_SEASON))
        if has_weekly
        else Decimal("0.00")
    )
    season_pool = _money(total_pool * season_percent / Decimal("100")) if has_season else Decimal("0.00")

    last_place = None
    if has_season and last_percent > 0:
        last_place = {
            "label": "Last place",
            "percent": last_percent,
            "amount": _money(season_pool * last_percent / Decimal("100")),
        }

    return {
        "entry_fee": _money(entry_fee),
        "member_count": member_count,
        "total_pool": total_pool,
        "weeks_in_season": WEEKS_IN_SEASON,
        "weekly_percent": weekly_percent,
        "season_percent": season_percent,
        "weekly_pool": weekly_pool,
        "season_pool": season_pool,
        "weekly_places": _place_rows(league_rules.weekly_payout_structure, weekly_pool),
        "season_places": _place_rows(league_rules.season_payout_structure, season_pool),
        "last_place": last_place,
        "has_entry": has_entry,
        "has_weekly": has_weekly,
        "has_season": has_season,
    }
