from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from django.utils import timezone

from cfb.models import Game, League, LeagueRules, Week

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


def league_week_slate(league: League, week: Week):
    """Active games selected for a league in a given week."""
    return Game.objects.filter(
        week=week,
        league_selections__league=league,
        league_selections__is_active=True,
    )


def is_week_slate_final(league: Optional[League], week: Optional[Week]) -> bool:
    """
    True when the league has selected games for the week and all of them are final.
    Weeks with no selected games are not considered complete.
    """
    if not league or not week:
        return False

    slate = league_week_slate(league, week)
    if not slate.exists():
        return False

    return not slate.exclude(is_final=True).exists()


def is_league_season_final(league: Optional[League], league_rules: Optional[LeagueRules]) -> bool:
    """
    True when the manager-set season end week has an active slate and either
    all those games are final or the week's end date has passed.
    """
    if not league or not league_rules or not league_rules.season_end_week_id:
        return False

    week = league_rules.season_end_week
    slate = league_week_slate(league, week)
    if not slate.exists():
        return False

    if timezone.localdate() > week.end_date:
        return True

    return not slate.exclude(is_final=True).exists()


def _place_span_label(start_place: int, end_place: int, tied: bool) -> str:
    if start_place == end_place:
        base = f"{_ordinal(start_place)} place"
    else:
        base = f"{_ordinal(start_place)}–{_ordinal(end_place)}"
    return f"{base} (tie)" if tied else base


def attach_prize_amounts(
    standings: List[Dict[str, Any]],
    places: Optional[List[Dict[str, Any]]],
    last_place: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Attach prize dollars to ranked standings rows (mutates in place).

    Uses standard dead-heat splitting after competition ranks:
    N players tied at rank R split the sum of place pots R .. R+N-1
    (only pots that exist) evenly. Example: two tied for 1st with three
    paid spots split 1st+2nd; the next finisher gets 3rd. Same rule for
    ties at 2nd or lower.
    """
    for row in standings:
        row["prize_amount"] = None
        row["prize_label"] = None

    if not standings:
        return

    place_amounts = {
        int(place["place"]): _as_decimal(place["amount"])
        for place in (places or [])
        if place.get("place") is not None
    }

    # Process in rank order; group identical competition ranks.
    ordered = sorted(
        enumerate(standings),
        key=lambda item: (item[1].get("display_rank") or 10**9, item[0]),
    )

    i = 0
    while i < len(ordered):
        _, row = ordered[i]
        rank = row.get("display_rank")
        group = [ordered[i]]
        j = i + 1
        while j < len(ordered) and ordered[j][1].get("display_rank") == rank:
            group.append(ordered[j])
            j += 1

        if rank is None or rank <= 0:
            i = j
            continue

        tied_count = len(group)
        start_place = int(rank)
        end_place = start_place + tied_count - 1

        pot = Decimal("0")
        paid_start = None
        paid_end = None
        for place_num in range(start_place, end_place + 1):
            amount = place_amounts.get(place_num)
            if amount is None:
                continue
            pot += amount
            if paid_start is None:
                paid_start = place_num
            paid_end = place_num

        if pot > 0 and paid_start is not None and paid_end is not None:
            share = _money(pot / Decimal(tied_count))
            label = _place_span_label(paid_start, paid_end, tied=tied_count > 1)
            for _, group_row in group:
                group_row["prize_amount"] = share
                group_row["prize_label"] = label

        i = j

    if last_place:
        last_rank = max((row.get("display_rank") or 0) for row in standings)
        last_group = [
            row
            for row in standings
            if row.get("display_rank") == last_rank and row.get("prize_amount") is None
        ]
        if last_group:
            share = _money(_as_decimal(last_place["amount"]) / Decimal(len(last_group)))
            label = last_place["label"]
            if len(last_group) > 1:
                label = f"{label} (tie)"
            for row in last_group:
                row["prize_amount"] = share
                row["prize_label"] = label


def attach_season_prize_amounts(
    standings: List[Dict[str, Any]],
    payout_summary: Optional[Dict[str, Any]],
) -> None:
    """Attach season prize dollars to ranked standings rows (mutates in place)."""
    if not payout_summary or not payout_summary.get("has_season"):
        attach_prize_amounts(standings, None)
        return
    attach_prize_amounts(
        standings,
        payout_summary.get("season_places"),
        payout_summary.get("last_place"),
    )


def attach_weekly_prize_amounts(
    standings: List[Dict[str, Any]],
    payout_summary: Optional[Dict[str, Any]],
) -> None:
    """Attach weekly prize dollars to ranked week standings rows (mutates in place)."""
    if not payout_summary or not payout_summary.get("has_weekly"):
        attach_prize_amounts(standings, None)
        return
    attach_prize_amounts(standings, payout_summary.get("weekly_places"))


def _prize_lookup_by_user(
    rank_by_user_id: Dict[int, int],
    places: Optional[List[Dict[str, Any]]],
    last_place: Optional[Dict[str, Any]] = None,
) -> Dict[int, Dict[str, Any]]:
    rows = [
        {"user_id": user_id, "display_rank": rank}
        for user_id, rank in rank_by_user_id.items()
    ]
    attach_prize_amounts(rows, places, last_place)
    return {
        row["user_id"]: {
            "amount": row.get("prize_amount"),
            "label": row.get("prize_label"),
        }
        for row in rows
    }


def _sum_weekly_amounts_by_user(
    completed_week_ranks: List[Dict[int, int]],
    payout_summary: Dict[str, Any],
) -> Dict[int, Decimal]:
    """Sum weekly place payouts across completed weeks for each user."""
    totals: Dict[int, Decimal] = {}
    if not payout_summary.get("has_weekly"):
        return totals

    places = payout_summary.get("weekly_places")
    for rank_by_user in completed_week_ranks:
        if not rank_by_user:
            continue
        lookup = _prize_lookup_by_user(rank_by_user, places)
        for user_id, prize in lookup.items():
            amount = prize.get("amount")
            if amount is None:
                continue
            totals[user_id] = totals.get(user_id, Decimal("0")) + _as_decimal(amount)
    return {user_id: _money(total) for user_id, total in totals.items()}


def apply_standings_money_columns(
    standings: List[Dict[str, Any]],
    payout_summary: Optional[Dict[str, Any]],
    *,
    completed_week_ranks: Optional[List[Dict[int, int]]] = None,
    projected_week_ranks: Optional[Dict[int, int]] = None,
    season_ranks: Optional[Dict[int, int]] = None,
    include_weeks_won: bool = False,
    include_projected_week: bool = False,
    include_season: bool = False,
) -> None:
    """
    Attach money columns:
    - weeks_won_amount: sum of completed weekly payouts
    - projected_week_amount: as-is payout for the in-progress week
    - season_prize_amount: final or projected season payout
    - prize_total_amount: sum of included columns
    """
    for row in standings:
        row["weeks_won_amount"] = None
        row["projected_week_amount"] = None
        row["projected_week_label"] = None
        row["season_prize_amount"] = None
        row["season_prize_label"] = None
        row["prize_total_amount"] = None
        row["week_prize_amount"] = None
        row["week_prize_label"] = None
        row["prize_amount"] = None
        row["prize_label"] = None

    if not standings or not payout_summary:
        return

    weeks_won_lookup = (
        _sum_weekly_amounts_by_user(completed_week_ranks or [], payout_summary)
        if include_weeks_won
        else {}
    )

    projected_week_lookup: Dict[int, Dict[str, Any]] = {}
    if (
        include_projected_week
        and payout_summary.get("has_weekly")
        and projected_week_ranks
    ):
        projected_week_lookup = _prize_lookup_by_user(
            projected_week_ranks,
            payout_summary.get("weekly_places"),
        )

    season_lookup: Dict[int, Dict[str, Any]] = {}
    if include_season and payout_summary.get("has_season") and season_ranks:
        season_lookup = _prize_lookup_by_user(
            season_ranks,
            payout_summary.get("season_places"),
            payout_summary.get("last_place"),
        )

    for row in standings:
        user = row.get("user")
        user_id = getattr(user, "id", None)
        if user_id is None:
            continue

        total = Decimal("0")
        has_any = False

        if include_weeks_won:
            won = weeks_won_lookup.get(user_id, Decimal("0.00"))
            row["weeks_won_amount"] = _money(won)
            total += _as_decimal(row["weeks_won_amount"])
            has_any = True

        if include_projected_week:
            projected = projected_week_lookup.get(user_id, {})
            row["projected_week_amount"] = projected.get("amount")
            row["projected_week_label"] = projected.get("label")
            if row["projected_week_amount"] is not None:
                total += _as_decimal(row["projected_week_amount"])
                has_any = True

        if include_season:
            season_prize = season_lookup.get(user_id, {})
            row["season_prize_amount"] = season_prize.get("amount")
            row["season_prize_label"] = season_prize.get("label")
            if row["season_prize_amount"] is not None:
                total += _as_decimal(row["season_prize_amount"])
                has_any = True

        row["prize_total_amount"] = _money(total) if has_any else None


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
