import logging
import smtplib

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from cfb.models import LeagueMembership

logger = logging.getLogger(__name__)


def pending_opt_in_members(league):
    return (
        LeagueMembership.objects.filter(league=league, is_active=False, role="member")
        .select_related("user")
        .order_by("user__username")
    )


def send_season_opt_in_emails(request, league, season):
    """Email inactive members a personal opt-in link for this season."""
    sent = 0
    skipped = 0
    failed = 0

    for membership in pending_opt_in_members(league):
        email = (membership.user.email or "").strip()
        if not email:
            skipped += 1
            continue

        token = membership.get_opt_in_token(season.year)
        activate_url = request.build_absolute_uri(
            reverse("league_opt_in", kwargs={"token": token})
        )
        context = {
            "league": league,
            "season": season,
            "user": membership.user,
            "activate_url": activate_url,
        }
        body = render_to_string("cfb/email/season_opt_in.txt", context)
        try:
            send_mail(
                subject=f"Rejoin {league.name} for {season.year}",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            sent += 1
        except (OSError, smtplib.SMTPException, BadHeaderError):
            logger.exception(
                "Failed to send season opt-in email to %s for league %s",
                email,
                league.pk,
            )
            failed += 1

    return sent, skipped, failed
