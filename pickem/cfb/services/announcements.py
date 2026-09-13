import logging
import smtplib

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from cfb.models import LeagueMembership, user_notification_emails

logger = logging.getLogger(__name__)


def active_announcement_recipients(league):
    return (
        LeagueMembership.objects.filter(league=league, is_active=True)
        .select_related("user", "user__profile")
        .order_by("user__username")
    )


def send_announcement_emails(request, announcement):
    """Email active league members about a new announcement."""
    sent = 0
    skipped = 0
    failed = 0
    league = announcement.league
    home_url = request.build_absolute_uri(reverse("home"))

    for membership in active_announcement_recipients(league):
        emails = user_notification_emails(membership.user)
        if not emails:
            skipped += 1
            continue

        body = render_to_string(
            "cfb/email/league_announcement.txt",
            {
                "user": membership.user,
                "league": league,
                "announcement": announcement,
                "home_url": home_url,
            },
        )
        try:
            send_mail(
                subject=f"{league.name}: {announcement.title}",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=emails,
                fail_silently=False,
            )
        except (OSError, smtplib.SMTPException, BadHeaderError):
            logger.exception(
                "Failed to send announcement email to %s for league %s announcement %s",
                ", ".join(emails),
                league.id,
                announcement.id,
            )
            failed += 1
            continue
        sent += 1

    return sent, skipped, failed
