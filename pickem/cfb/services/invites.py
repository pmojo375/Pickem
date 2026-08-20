import logging
import smtplib
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import BadHeaderError, send_mail
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.urls import reverse

from cfb.models import LeagueMembership

from . import opt_in

logger = logging.getLogger(__name__)
User = get_user_model()


def normalize_invite_email(raw_email: str) -> str:
    email = (raw_email or "").strip()
    if not email:
        raise ValidationError("Enter an email address.")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationError("Enter a valid email address.") from exc
    return email


def send_league_email_invite(request, league, raw_email, season=None):
    """
    Email an invite for this league.

    - Existing inactive member: season opt-in email (same as bulk returning-member mail)
    - Existing user not in the league: league invite link
    - Unknown email: signup + join league email
    """
    email = normalize_invite_email(raw_email)
    user = User.objects.filter(email__iexact=email).first()

    if user:
        membership = LeagueMembership.objects.filter(league=league, user=user).first()
        if membership and membership.is_active:
            return "already_active", email
        if membership and not membership.is_active:
            if season is None:
                return "no_season", email
            if opt_in.send_single_season_opt_in_email(request, membership, season):
                return "opt_in_sent", email
            return "failed", email

        invite_url = request.build_absolute_uri(league.get_invite_path())
        body = render_to_string(
            "cfb/email/league_invite_existing.txt",
            {
                "league": league,
                "user": user,
                "inviter": request.user,
                "invite_url": invite_url,
            },
        )
        subject = f"You're invited to join {league.name}"
        kind = "existing_sent"
    else:
        invite_url = request.build_absolute_uri(league.get_invite_path())
        signup_path = reverse("account_signup")
        signup_url = request.build_absolute_uri(
            f"{signup_path}?{urlencode({'next': league.get_invite_path()})}"
        )
        body = render_to_string(
            "cfb/email/league_invite_new.txt",
            {
                "league": league,
                "inviter": request.user,
                "signup_url": signup_url,
                "invite_url": invite_url,
            },
        )
        subject = f"Join CFB Pick'em and play in {league.name}"
        kind = "new_sent"

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send league invite email to %s for league %s",
            email,
            league.pk,
        )
        return "failed", email

    return kind, email
