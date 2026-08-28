import logging
import smtplib

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import BadHeaderError, send_mail
from django.core.validators import validate_email
from django.template.loader import render_to_string
from django.utils import timezone

from cfb.models import LeagueInvite, LeagueMembership

from . import opt_in

logger = logging.getLogger(__name__)
User = get_user_model()

PERSONAL_INVITE_TOKEN_SESSION_KEY = "personal_invite_token"


def normalize_invite_email(raw_email: str) -> str:
    email = (raw_email or "").strip()
    if not email:
        raise ValidationError("Enter an email address.")
    try:
        validate_email(email)
    except ValidationError as exc:
        raise ValidationError("Enter a valid email address.") from exc
    return email.lower()


def get_personal_invite(token: str):
    if not token:
        return None
    return LeagueInvite.objects.filter(token=token).select_related("league").first()


def invite_status(invite) -> str:
    """Return invite state: pending, accepted, expired, revoked, or missing."""
    if invite is None:
        return "missing"
    if invite.is_accepted:
        return "accepted"
    if invite.is_revoked:
        return "revoked"
    if invite.is_expired:
        return "expired"
    return "pending"


def user_has_verified_email(user, email: str) -> bool:
    return EmailAddress.objects.filter(
        user=user,
        email__iexact=email,
        verified=True,
    ).exists()


def accept_personal_invite(invite, user):
    """
    Accept a personal invite for the given user.

    Returns one of:
    joined, reactivated, already_member, already_accepted, email_mismatch, invalid
    """
    status = invite_status(invite)
    if status == "accepted":
        return "already_accepted"
    if status != "pending":
        return "invalid"

    if not user_has_verified_email(user, invite.email):
        return "email_mismatch"

    league = invite.league
    existing = LeagueMembership.objects.filter(league=league, user=user).first()
    now = timezone.now()

    if existing:
        if existing.is_active:
            invite.accepted_at = now
            invite.save(update_fields=["accepted_at"])
            return "already_member"
        existing.is_active = True
        existing.save(update_fields=["is_active"])
        invite.accepted_at = now
        invite.save(update_fields=["accepted_at"])
        return "reactivated"

    LeagueMembership.objects.create(league=league, user=user, role="member")
    invite.accepted_at = now
    invite.save(update_fields=["accepted_at"])
    return "joined"


def create_personal_invite(league, raw_email: str, invited_by=None) -> LeagueInvite:
    email = normalize_invite_email(raw_email)
    return LeagueInvite.create_for_email(league, email, invited_by=invited_by)


def _send_personal_invite_email(request, league, invite, inviter, user=None) -> bool:
    invite_url = request.build_absolute_uri(invite.get_path())
    if user:
        body = render_to_string(
            "cfb/email/league_invite_existing.txt",
            {
                "league": league,
                "user": user,
                "inviter": inviter,
                "invite_url": invite_url,
            },
        )
        subject = f"You're invited to join {league.name}"
    else:
        body = render_to_string(
            "cfb/email/league_invite_new.txt",
            {
                "league": league,
                "inviter": inviter,
                "invite_url": invite_url,
            },
        )
        subject = f"Join BigPicks and play in {league.name}"

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invite.email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, BadHeaderError):
        logger.exception(
            "Failed to send personal league invite email to %s for league %s",
            invite.email,
            league.pk,
        )
        return False
    return True


def send_league_email_invite(request, league, raw_email, season=None):
    """
    Email a personal league invite for this league.

    - Existing inactive member: season opt-in email (same as bulk returning-member mail)
    - Existing user not in the league: personal invite link
    - Unknown email: personal invite link (signup handled on the invite page)
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

        invite = create_personal_invite(league, email, invited_by=request.user)
        if not _send_personal_invite_email(request, league, invite, request.user, user=user):
            return "failed", email
        return "existing_sent", email

    invite = create_personal_invite(league, email, invited_by=request.user)
    if not _send_personal_invite_email(request, league, invite, request.user):
        return "failed", email
    return "new_sent", email


def send_league_email_invites_bulk(request, league, raw_emails, season=None):
    """Send personal league invites to multiple email addresses."""
    results = []
    for raw_email in raw_emails:
        try:
            results.append(send_league_email_invite(request, league, raw_email, season=season))
        except ValidationError:
            results.append(("invalid", (raw_email or "").strip()))
    return results
