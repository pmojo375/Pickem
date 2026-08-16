import logging
import smtplib

from allauth.account.adapter import DefaultAccountAdapter
from django.core.mail import BadHeaderError

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
