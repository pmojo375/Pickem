import logging
import smtplib

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from cfb.models import LeagueMembership, user_notification_emails

logger = logging.getLogger(__name__)


def pending_opt_in_members(league):
    return (
        LeagueMembership.objects.filter(league=league, is_active=False, role="member")
        .select_related("user", "user__profile")
        .order_by("user__username")
    )


def send_single_season_opt_in_email(request, membership, season) -> bool:
    """Email one inactive member a personal opt-in link. Returns True on success."""
    emails = user_notification_emails(membership.user)
    if not emails:
        return False

    token = membership.get_opt_in_token(season.year)
    activate_url = request.build_absolute_uri(
        reverse("league_opt_in", kwargs={"token": token})
    )
    body = render_to_string(
        "cfb/email/season_opt_in.txt",
        {
            "league": membership.league,
            "season": season,
            "user": membership.user,
            "activate_url": activate_url,
        },
    )
    try:
        send_mail(
            subject=f"Rejoin {membership.league.name} for {season.year}",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=emails,
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send season opt-in email to %s for league %s",
            ", ".join(emails),
            membership.league_id,
        )
        return False
    return True


def send_season_opt_in_emails(request, league, season):
    """Email inactive members a personal opt-in link for this season."""
    sent = 0
    skipped = 0
    failed = 0

    for membership in pending_opt_in_members(league):
        emails = user_notification_emails(membership.user)
        if not emails:
            skipped += 1
            continue

        if send_single_season_opt_in_email(request, membership, season):
            sent += 1
        else:
            failed += 1

    return sent, skipped, failed
