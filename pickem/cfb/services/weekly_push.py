import json
import logging
from datetime import timedelta
from time import monotonic

import requests
from django.conf import settings
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from py_vapid import VapidException
from pywebpush import WebPushException, webpush

from ..models import LeagueGame, LeagueMembership, PushSubscription, WeeklyPickPush
from .schedule import get_display_week, get_week_datetime_range

logger = logging.getLogger(__name__)


def slate_games(league, week):
    # The Picks and Settings pages group games by kickoff, including reschedules.
    return LeagueGame.objects.filter(
        league=league, game__season_id=week.season_id,
        game__kickoff__range=get_week_datetime_range(week),
    )


def queue_weekly_pushes(league, week):
    devices = PushSubscription.objects.select_for_update(of=("self",)).filter(
        user__is_active=True, user__league_memberships__league=league,
        user__league_memberships__is_active=True,
    ).values_list("pk", flat=True)
    WeeklyPickPush.objects.bulk_create([
        WeeklyPickPush(subscription_id=device_id, league=league, week=week)
        for device_id in devices
    ], ignore_conflicts=True)


def deliver_pending_pushes():
    if not (settings.WEB_PUSH_PRIVATE_KEY and settings.WEB_PUSH_PUBLIC_KEY):
        return
    due_ids = list(
        WeeklyPickPush.objects.filter(next_attempt_at__lte=timezone.now())
        .order_by("next_attempt_at", "pk")
        .values_list("pk", flat=True)[:50]
    )
    if not due_ids:
        return
    week = get_display_week()
    deadline = monotonic() + 20
    with requests.Session() as session:
        session.max_redirects = 0
        for push_id in due_ids:
            if monotonic() >= deadline:
                break
            # A short lease also releases a notification if a worker dies mid-send.
            now = timezone.now()
            claimed = WeeklyPickPush.objects.filter(pk=push_id, next_attempt_at__lte=now).update(
                next_attempt_at=now + timedelta(minutes=5), attempts=F("attempts") + 1,
            )
            if claimed:
                deliver_one(push_id, week, session)


def deliver_one(push_id, week, session):
    push = WeeklyPickPush.objects.select_related("league", "subscription").filter(pk=push_id).first()
    if push is None:
        return  # The device unsubscribed.
    device = push.subscription
    active_membership = LeagueMembership.objects.filter(
        league_id=push.league_id, user_id=device.user_id, is_active=True, user__is_active=True
    )
    if not push.league.is_active or not active_membership.exists():
        push.delete()
        return
    if push.event != "published":
        from .slate_alerts import event_still_wanted
        if push.expires_at <= timezone.now() or not event_still_wanted(push):
            push.next_attempt_at = None
            push.save(update_fields=["next_attempt_at"])
            return
        send_payload(push, push.payload, push.expires_at, session)
        return
    if not week or week.pk != push.week_id:
        push.delete()
        return
    last_kickoff = slate_games(push.league, week).filter(
        is_active=True, game__is_final=False, game__kickoff__gt=timezone.now()
    ).order_by("-game__kickoff").values_list("game__kickoff", flat=True).first()
    if not last_kickoff:
        push.delete()
        return

    payload = {
        "title": f"Week {week.number} picks are ready",
        "body": f"{push.league.name} posted its games. Tap to make your picks.",
        "url": f"{reverse('picks')}?league_id={push.league_id}",
        "tag": f"picks-{push.league_id}-{week.pk}",
    }
    send_payload(push, payload, last_kickoff, session)


def send_payload(push, payload, expires, session):
    device = push.subscription
    push_id = push.pk
    try:
        webpush(
            subscription_info={"endpoint": device.endpoint, "keys": {"p256dh": device.p256dh, "auth": device.auth}},
            data=json.dumps(payload), vapid_private_key=settings.WEB_PUSH_PRIVATE_KEY,
            vapid_claims={"sub": settings.WEB_PUSH_SUBJECT}, requests_session=session,
            ttl=max(0, min(86400, int((expires - timezone.now()).total_seconds()))), timeout=5,
        )
    except (WebPushException, requests.RequestException) as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if response is not None else None
        if status in (404, 410):
            device.delete()  # Cascades to any other pending notifications for this installation.
            return
        # Endpoints and encryption keys are private, including in provider error messages.
        logger.warning("Weekly push %s failed (HTTP %s, attempt %s)", push_id, status, push.attempts)
        if push.attempts < 5 and (status is None or status in (408, 429) or status >= 500):
            WeeklyPickPush.objects.filter(pk=push_id).update(
                next_attempt_at=timezone.now() + timedelta(minutes=2 ** push.attempts)
            )
            return
    except (VapidException, OSError, ValueError):
        # A missing or unreadable signing key is shared by all installations.
        # Keep this delivery pending so repairing worker config can recover it.
        logger.error("Unable to sign weekly push %s. Check Web Push credentials.", push_id)
        return
    if push.event == "published":
        push.delete()
    else:
        WeeklyPickPush.objects.filter(pk=push_id).update(next_attempt_at=None)
