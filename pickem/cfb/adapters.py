import logging
import smtplib

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.mail import BadHeaderError

from cfb.services.invites import (
    PERSONAL_INVITE_TOKEN_SESSION_KEY,
    accept_personal_invite,
    get_personal_invite,
    get_user_for_invite_email,
)

logger = logging.getLogger(__name__)


class AccountAdapter(DefaultAccountAdapter):
    """Keep signup/login working if outbound email cannot be delivered."""

    def send_mail(self, template_prefix, email, context):
        try:
            super().send_mail(template_prefix, email, context)
        except (OSError, smtplib.SMTPException, BadHeaderError):
            logger.exception(
                "Failed to send %s email to %s",
                template_prefix,
                email,
            )


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Connect Google sign-in to personal league invitations."""

    def pre_social_login(self, request, sociallogin):
        token = request.session.get(PERSONAL_INVITE_TOKEN_SESSION_KEY)
        if not token:
            return

        invite = get_personal_invite(token)
        if invite is None or not invite.is_pending:
            return

        social_email = (sociallogin.user.email or "").strip().lower()
        if social_email != invite.email.lower():
            sociallogin.state["personal_invite_email_mismatch"] = True
            return

        existing_user = get_user_for_invite_email(invite.email)
        if existing_user is None:
            return

        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=existing_user,
            email=invite.email.lower(),
            defaults={"verified": True, "primary": True},
        )
        if existing_user.email.lower() != invite.email.lower():
            existing_user.email = invite.email.lower()
            existing_user.save(update_fields=["email"])

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form=form)
        token = request.session.get(PERSONAL_INVITE_TOKEN_SESSION_KEY)
        if not token:
            return user

        invite = get_personal_invite(token)
        if invite is None or not invite.is_pending:
            return user

        from allauth.account.models import EmailAddress

        EmailAddress.objects.update_or_create(
            user=user,
            email=invite.email.lower(),
            defaults={"verified": True, "primary": True},
        )
        if user.email.lower() != invite.email.lower():
            user.email = invite.email.lower()
            user.save(update_fields=["email"])

        return user

    def get_connect_redirect_url(self, request, socialaccount):
        token = request.session.get(PERSONAL_INVITE_TOKEN_SESSION_KEY)
        if token:
            invite = get_personal_invite(token)
            if invite is not None:
                return invite.get_path()
        return super().get_connect_redirect_url(request, socialaccount)
