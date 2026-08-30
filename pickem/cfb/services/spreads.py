from datetime import date, timedelta

from django.utils import timezone

from ..models import Game, GameSpread


def get_spread_lock_target_date(game: Game, spread_lock_weekday: int) -> date:
    """
    Return the spread lock date for a game.

    Uses the configured weekday on or before kickoff, walking back to the prior
    occurrence when kickoff falls on the lock weekday itself.
    """
    game_date = timezone.localtime(game.kickoff).date()
    days_back = (game_date.weekday() - spread_lock_weekday) % 7
    if days_back == 0:
        days_back = 7
    return game_date - timedelta(days=days_back)


def get_spread_to_lock(
    game: Game,
    spread_lock_weekday: int,
    today: date | None = None,
) -> tuple[GameSpread | None, date]:
    """
    Return the spread that should be locked now, if any, plus the lock target date.

    Locking rules:
    1. Do nothing before the lock target date.
    2. On the lock date, only lock if a spread exists from that date.
    3. After the lock date, prefer spread from lock date, then next spread, then latest.
    """
    if today is None:
        today = timezone.now().date()

    lock_target_date = get_spread_lock_target_date(game, spread_lock_weekday)
    game_spreads = GameSpread.objects.filter(game=game).order_by("timestamp")

    if not game_spreads.exists() or today < lock_target_date:
        return None, lock_target_date

    if today == lock_target_date:
        spread_from_lock_day = game_spreads.filter(timestamp__date=lock_target_date).first()
        return spread_from_lock_day, lock_target_date

    spread_to_use = None
    for spread in game_spreads:
        if spread.timestamp.date() == lock_target_date:
            spread_to_use = spread
            break

    if not spread_to_use:
        for spread in game_spreads:
            if spread.timestamp.date() > lock_target_date:
                spread_to_use = spread
                break

    if not spread_to_use:
        spread_to_use = game_spreads.last()

    return spread_to_use, lock_target_date
