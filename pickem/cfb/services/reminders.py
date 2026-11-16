"""Pick reminder emails for members with incomplete period picks."""
import logging
import smtplib
from datetime import timedelta
from typing import Optional
from urllib.parse import urljoin

from django.conf import settings
from django.core.cache import cache
from django.core.mail import BadHeaderError, send_mail
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from cfb.models import LeagueGame, LeagueMembership, LeagueRules, Pick, SchedulePeriod, user_notification_emails

logger = logging.getLogger(__name__)


def reminder_cache_key(league_id: int, schedule_period_id: int) -> str:
    return f"{settings.REDIS_KEY_PICK_REMINDER_PREFIX}{league_id}:{schedule_period_id}"


def already_sent(league_id: int, schedule_period_id: int) -> bool:
    return bool(cache.get(reminder_cache_key(league_id, schedule_period_id)))


def mark_sent(league_id: int, schedule_period_id: int) -> None:
    cache.set(
        reminder_cache_key(league_id, schedule_period_id),
        True,
        timeout=settings.REDIS_KEY_PICK_REMINDER_TTL,
    )


def first_league_start_time(league, schedule_period: SchedulePeriod):
    return (
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__schedule_period=schedule_period,
            game__season=schedule_period.season,
        )
        .order_by("game__start_time")
        .values_list("game__start_time", flat=True)
        .first()
    )


def required_pick_count(rules: LeagueRules, period_game_count: int) -> int:
    if rules.picks_per_period and rules.picks_per_period > 0:
        return min(rules.picks_per_period, period_game_count)
    return period_game_count


def incomplete_members(league, schedule_period: SchedulePeriod, rules: LeagueRules):
    """Active members missing required picks and/or key picks for the schedule_period."""
    period_game_ids = list(
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__schedule_period=schedule_period,
            game__season=schedule_period.season,
        ).values_list("game_id", flat=True)
    )
    if not period_game_ids:
        return []

    required_picks = required_pick_count(rules, len(period_game_ids))
    required_key_picks = (
        rules.number_of_key_picks if rules.key_picks_enabled else 0
    )

    pick_stats = {
        row["user_id"]: row
        for row in (
            Pick.objects.filter(league=league, game_id__in=period_game_ids)
            .values("user_id")
            .annotate(
                pick_count=Count("id"),
                key_pick_count=Count("id", filter=Q(is_key_pick=True)),
            )
        )
    }

    incomplete = []
    memberships = (
        LeagueMembership.objects.filter(league=league, is_active=True)
        .select_related("user", "user__profile")
        .order_by("user__username")
    )
    for membership in memberships:
        stats = pick_stats.get(membership.user_id, {})
        pick_count = stats.get("pick_count", 0)
        key_pick_count = stats.get("key_pick_count", 0)

        missing_picks = max(0, required_picks - pick_count)
        missing_key_picks = max(0, required_key_picks - key_pick_count)
        if missing_picks or missing_key_picks:
            incomplete.append(
                {
                    "membership": membership,
                    "user": membership.user,
                    "picks_made": pick_count,
                    "picks_required": required_picks,
                    "missing_picks": missing_picks,
                    "key_picks_made": key_pick_count,
                    "key_picks_required": required_key_picks,
                    "missing_key_picks": missing_key_picks,
                }
            )
    return incomplete


def _absolute_picks_url(league_id: int) -> str:
    path = reverse("picks")
    return urljoin(f"{settings.SITE_URL}/", f"{path.lstrip('/')}?league_id={league_id}")


def send_pick_reminder_email(user, league, schedule_period: SchedulePeriod, first_start_time, status: dict) -> bool:
    emails = user_notification_emails(user)
    if not emails:
        return False

    body = render_to_string(
        "cfb/email/pick_reminder.txt",
        {
            "user": user,
            "league": league,
            "schedule_period": schedule_period,
            "first_start_time": timezone.localtime(first_start_time),
            "picks_url": _absolute_picks_url(league.id),
            "picks_made": status["picks_made"],
            "picks_required": status["picks_required"],
            "missing_picks": status["missing_picks"],
            "key_picks_enabled": status["key_picks_required"] > 0,
            "key_picks_made": status["key_picks_made"],
            "key_picks_required": status["key_picks_required"],
            "missing_key_picks": status["missing_key_picks"],
            "hours_before": settings.PICK_REMINDER_HOURS_BEFORE_KICKOFF,
        },
    )
    try:
        send_mail(
            subject=f"Reminder: finish your {league.name} picks ({schedule_period.display_name})",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=emails,
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send pick reminder to %s for league %s schedule_period %s",
            ", ".join(emails),
            league.id,
            schedule_period.id,
        )
        return False
    return True


def process_league_reminders(
    rules: LeagueRules,
    schedule_period: SchedulePeriod,
    *,
    before_start_time: Optional[timedelta] = None,
    now=None,
    force: bool = False,
    dry_run: bool = False,
    clear_sent: bool = False,
) -> dict:
    """
    If now is within [first_start_time - before_start_time, first_start_time), email
    incomplete active members once per league/schedule_period.

    force: ignore the time window (still respects already_sent unless clear_sent).
    dry_run: report recipients without sending or marking sent.
    clear_sent: drop the Redis dedupe key before running.
    """
    now = now or timezone.now()
    before_start_time = (
        settings.PICK_REMINDER_BEFORE_KICKOFF
        if before_start_time is None
        else before_start_time
    )
    league = rules.league
    result = {
        "league_id": league.id,
        "schedule_period_id": schedule_period.id,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "would_send": 0,
        "recipients": [],
        "status": "ok",
        "first_start_time": None,
        "reminder_at": None,
    }

    if clear_sent:
        cache.delete(reminder_cache_key(league.id, schedule_period.id))

    if already_sent(league.id, schedule_period.id) and not dry_run:
        result["status"] = "already_sent"
        return result

    first_start_time = first_league_start_time(league, schedule_period)
    if not first_start_time:
        result["status"] = "no_games"
        return result

    reminder_at = first_start_time - before_start_time
    result["first_start_time"] = first_start_time
    result["reminder_at"] = reminder_at

    if not force:
        if now < reminder_at:
            result["status"] = "too_early"
            return result
        if now >= first_start_time:
            result["status"] = "past_start_time"
            return result

    members = incomplete_members(league, schedule_period, rules)
    for status in members:
        emails = user_notification_emails(status["user"])
        if not emails:
            result["skipped"] += 1
            continue

        result["recipients"].append(
            {
                "username": status["user"].username,
                "email": ", ".join(emails),
                "picks_made": status["picks_made"],
                "picks_required": status["picks_required"],
                "key_picks_made": status["key_picks_made"],
                "key_picks_required": status["key_picks_required"],
            }
        )

        if dry_run:
            result["would_send"] += 1
            continue

        if send_pick_reminder_email(
            status["user"], league, schedule_period, first_start_time, status
        ):
            result["sent"] += 1
        else:
            result["failed"] += 1

    if dry_run:
        result["status"] = "dry_run"
        return result

    # Mark processed so we only attempt once per league/schedule_period in the window.
    mark_sent(league.id, schedule_period.id)
    result["status"] = "processed"
    return result
