"""Pick reminder emails for members with incomplete weekly picks."""
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

from cfb.models import LeagueGame, LeagueMembership, LeagueRules, Pick, Week

logger = logging.getLogger(__name__)


def reminder_cache_key(league_id: int, week_id: int) -> str:
    return f"{settings.REDIS_KEY_PICK_REMINDER_PREFIX}{league_id}:{week_id}"


def already_sent(league_id: int, week_id: int) -> bool:
    return bool(cache.get(reminder_cache_key(league_id, week_id)))


def mark_sent(league_id: int, week_id: int) -> None:
    cache.set(
        reminder_cache_key(league_id, week_id),
        True,
        timeout=settings.REDIS_KEY_PICK_REMINDER_TTL,
    )


def first_league_kickoff(league, week: Week):
    return (
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__week=week,
            game__season=week.season,
        )
        .order_by("game__kickoff")
        .values_list("game__kickoff", flat=True)
        .first()
    )


def required_pick_count(rules: LeagueRules, week_game_count: int) -> int:
    if rules.picks_per_week and rules.picks_per_week > 0:
        return min(rules.picks_per_week, week_game_count)
    return week_game_count


def incomplete_members(league, week: Week, rules: LeagueRules):
    """Active members missing required picks and/or key picks for the week."""
    week_game_ids = list(
        LeagueGame.objects.filter(
            league=league,
            is_active=True,
            game__week=week,
            game__season=week.season,
        ).values_list("game_id", flat=True)
    )
    if not week_game_ids:
        return []

    required_picks = required_pick_count(rules, len(week_game_ids))
    required_key_picks = (
        rules.number_of_key_picks if rules.key_picks_enabled else 0
    )

    pick_stats = {
        row["user_id"]: row
        for row in (
            Pick.objects.filter(league=league, game_id__in=week_game_ids)
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
        .select_related("user")
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


def send_pick_reminder_email(user, league, week: Week, first_kickoff, status: dict) -> bool:
    email = (user.email or "").strip()
    if not email:
        return False

    body = render_to_string(
        "cfb/email/pick_reminder.txt",
        {
            "user": user,
            "league": league,
            "week": week,
            "first_kickoff": timezone.localtime(first_kickoff),
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
            subject=f"Reminder: finish your {league.name} picks (Week {week.number})",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send pick reminder to %s for league %s week %s",
            email,
            league.id,
            week.id,
        )
        return False
    return True


def process_league_reminders(
    rules: LeagueRules,
    week: Week,
    *,
    before_kickoff: Optional[timedelta] = None,
    now=None,
    force: bool = False,
    dry_run: bool = False,
    clear_sent: bool = False,
) -> dict:
    """
    If now is within [first_kickoff - before_kickoff, first_kickoff), email
    incomplete active members once per league/week.

    force: ignore the time window (still respects already_sent unless clear_sent).
    dry_run: report recipients without sending or marking sent.
    clear_sent: drop the Redis dedupe key before running.
    """
    now = now or timezone.now()
    before_kickoff = (
        settings.PICK_REMINDER_BEFORE_KICKOFF
        if before_kickoff is None
        else before_kickoff
    )
    league = rules.league
    result = {
        "league_id": league.id,
        "week_id": week.id,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "would_send": 0,
        "recipients": [],
        "status": "ok",
        "first_kickoff": None,
        "reminder_at": None,
    }

    if clear_sent:
        cache.delete(reminder_cache_key(league.id, week.id))

    if already_sent(league.id, week.id) and not dry_run:
        result["status"] = "already_sent"
        return result

    first_kickoff = first_league_kickoff(league, week)
    if not first_kickoff:
        result["status"] = "no_games"
        return result

    reminder_at = first_kickoff - before_kickoff
    result["first_kickoff"] = first_kickoff
    result["reminder_at"] = reminder_at

    if not force:
        if now < reminder_at:
            result["status"] = "too_early"
            return result
        if now >= first_kickoff:
            result["status"] = "past_kickoff"
            return result

    members = incomplete_members(league, week, rules)
    for status in members:
        email = (status["user"].email or "").strip()
        if not email:
            result["skipped"] += 1
            continue

        result["recipients"].append(
            {
                "username": status["user"].username,
                "email": email,
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
            status["user"], league, week, first_kickoff, status
        ):
            result["sent"] += 1
        else:
            result["failed"] += 1

    if dry_run:
        result["status"] = "dry_run"
        return result

    # Mark processed so we only attempt once per league/week in the window.
    mark_sent(league.id, week.id)
    result["status"] = "processed"
    return result
